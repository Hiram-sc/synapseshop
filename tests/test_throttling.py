"""Testes de throttling (Aula 7, ETAPA 11).

O que é verificado:

* requisições **dentro** do limite passam;
* a requisição que **estoura** o limite recebe 429 com `Retry-After`;
* o critério é o declarado: por **IP** no login e nas leituras anônimas, por
  **usuário** autenticado nas escritas administrativas;
* a leitura pública não consome o contador de escrita (foi um bug real do
  primeiro esboço: o DRF lê o escopo da *view*, então trocar o escopo na
  instância da throttle não tem efeito).

Sobre as taxas: o teste de login usa a taxa real de `config/settings.py`
(5/min), para provar que a configuração de verdade está no caminho. Os demais
testes reduzem a taxa com `patch.object`, porque 30 ou 60 requisições para
cobrir um teto de escrita só adicionariam tempo de execução sem testar nada
novo - o mecanismo é o mesmo.
"""

from __future__ import annotations

from unittest.mock import patch

from rest_framework.test import APIClient

from api import throttling
from repositories.models import Role
from tests import ApiTestCaseBase

ITENS = "/api/v1/items/"
CATEGORIAS = "/api/v1/categories/"
LOGIN = "/api/v1/auth/login/"


class TestLimiteDoLogin(ApiTestCaseBase):
    """O login é o alvo preferencial da força bruta: 5/min por IP."""

    def test_01_cinco_tentativas_passam_e_a_sexta_e_suprimida(self) -> None:
        respostas = [
            self.client.post(
                LOGIN,
                {"username": "admin", "password": "senha-errada"},
                format="json",
            )
            for _ in range(5)
        ]
        self.assertEqual([r.status_code for r in respostas], [401] * 5)

        excedente = self.client.post(
            LOGIN, {"username": "admin", "password": "senha-errada"}, format="json"
        )
        self.assertEqual(excedente.status_code, 429)

    def test_02_resposta_429_traz_retry_after(self) -> None:
        for _ in range(5):
            self.client.post(
                LOGIN, {"username": "admin", "password": "x"}, format="json"
            )

        resposta = self.client.post(
            LOGIN, {"username": "admin", "password": "x"}, format="json"
        )

        self.assertEqual(resposta.status_code, 429)
        self.assertIn("Retry-After", resposta)
        self.assertGreater(int(resposta["Retry-After"]), 0)

    def test_03_429_nao_deixa_vazar_se_a_credencial_esta_certa(self) -> None:
        for _ in range(5):
            self.client.post(
                LOGIN, {"username": "admin", "password": "x"}, format="json"
            )

        # mesmo com a senha correta, a janela esgotada continua bloqueando
        resposta = self.client.post(
            LOGIN,
            {"username": "admin", "password": "senha-admin-de-teste"},
            format="json",
        )
        self.assertEqual(resposta.status_code, 429)
        self.assertNotIn("access", resposta.data)

    def test_04_limite_do_login_e_contado_por_ip(self) -> None:
        """Duas contas no mesmo IP compartilham a mesma janela."""
        with patch.object(
            throttling.LoginRateThrottle, "THROTTLE_RATES", {"login": "2/min"}
        ):
            for username in ("admin", "user"):
                self.client.post(
                    LOGIN, {"username": username, "password": "x"}, format="json"
                )
            self.client.post(
                LOGIN, {"username": "user", "password": "x"}, format="json"
            )

            excedente = self.client.post(
                LOGIN, {"username": "user", "password": "x"}, format="json"
            )

        self.assertEqual(excedente.status_code, 429)


class TestLimiteAnonimo(ApiTestCaseBase):
    """Leitura pública: 60/min por IP (taxa real de `config/settings.py`)."""

    def test_01_leitura_anonima_e_limitada_por_ip(self) -> None:
        with patch.object(
            throttling.AnonRateThrottle, "THROTTLE_RATES", {"anon": "2/min"}
        ):
            self.assertEqual(self.client.get(ITENS).status_code, 200)
            self.assertEqual(self.client.get(ITENS).status_code, 200)
            excedente = self.client.get(ITENS)

        self.assertEqual(excedente.status_code, 429)

    def test_02_autenticado_nao_gasta_a_janela_anonima(self) -> None:
        """O limite por usuário é outro contador, separado do anonimo."""
        with patch.object(
            throttling.AnonRateThrottle, "THROTTLE_RATES", {"anon": "1/min"}
        ):
            anonimo = APIClient()
            self.assertEqual(anonimo.get(ITENS).status_code, 200)
            self.assertEqual(anonimo.get(ITENS).status_code, 429)

            autenticado = APIClient()
            headers = self.auth_de(self.usuario)
            self.assertEqual(
                autenticado.get(ITENS, **headers).status_code, 200
            )


class TestLimiteDeEscritaAdmin(ApiTestCaseBase):
    """Escritas administrativas: 30/min por usuário autenticado."""

    def _escrever_repetidamente(self, quantidade: int) -> list:
        return [
            self.client.post(
                CATEGORIAS,
                {"name": f"Categoria {numero}"},
                format="json",
                **self.auth_de(self.admin),
            )
            for numero in range(quantidade)
        ]

    def test_01_escritas_dentro_do_limite_passam(self) -> None:
        with patch.object(
            throttling.AdminWriteThrottle,
            "THROTTLE_RATES",
            {"admin_write": "3/min"},
        ):
            respostas = self._escrever_repetidamente(3)
            self.assertEqual([r.status_code for r in respostas], [201] * 3)

    def test_02_escrita_que_excede_o_limite_responde_429(self) -> None:
        with patch.object(
            throttling.AdminWriteThrottle,
            "THROTTLE_RATES",
            {"admin_write": "2/min"},
        ):
            self._escrever_repetidamente(2)
            excedente = self.client.post(
                CATEGORIAS, {"name": "Mais uma"}, format="json",
                **self.auth_de(self.admin),
            )

        self.assertEqual(excedente.status_code, 429)
        self.assertIn("Retry-After", excedente)

    def test_03_leitura_publica_nao_usa_o_limite_de_escrita(self) -> None:
        """O escopo `admin_write` só vale para as ações que alteram dados."""
        with patch.object(
            throttling.AdminWriteThrottle,
            "THROTTLE_RATES",
            {"admin_write": "1/min"},
        ):
            self._escrever_repetidamente(1)
            self.client.post(
                CATEGORIAS, {"name": "Outra"}, format="json",
                **self.auth_de(self.admin),
            )

            leituras = [self.client.get(ITENS).status_code for _ in range(3)]

        self.assertEqual(leituras, [200, 200, 200])

    def test_04_limite_de_escrita_e_por_usuario(self) -> None:
        """O admin que estourou o limite não derruba o outro usuário."""
        with patch.object(
            throttling.AdminWriteThrottle,
            "THROTTLE_RATES",
            {"admin_write": "1/min"},
        ):
            headers = self.auth_de(self.admin)
            self.client.post(
                CATEGORIAS, {"name": "A"}, format="json", **headers
            )
            excedente = self.client.post(
                CATEGORIAS, {"name": "B"}, format="json", **headers
            )
            self.assertEqual(excedente.status_code, 429)

            outro_admin = self.users.create_user("admin2", "senha", "admin")
            self.assertEqual(
                self.client.post(
                    CATEGORIAS, {"name": "C"}, format="json",
                    **self.auth_de(outro_admin),
                ).status_code,
                201,
            )

    def test_05_escrita_sem_permissao_nao_gasta_o_limite_de_escrita(self) -> None:
        """Uma escrita negada por permissão não consome o limite de escrita.

        O DRF roda `check_permissions` **antes** de `check_throttles`
        (`APIView.initial`), então o 403 é decidido antes de o throttle ser
        consultado: uma escrita sem permissão não alcança o contador, e por
        isso não pode virar 429 na mesma rota. Esse é o comportamento correto
        - um `user` sem permissão não consegue esgotar a cota de escrita de um
        `admin`, nem a própria quando é promovido logo em seguida.

        O teste exige as duas metades da afirmação, senão ele não prova nada:
        as escritas permitidas depois da promoção (403 não descontou nada) e um
        429 de verdade no mesmo usuário, que só fica observável porque o
        contador é por `request.user.pk` (`ScopedRateThrottle.get_cache_key`).
        """
        with patch.object(
            throttling.AdminWriteThrottle,
            "THROTTLE_RATES",
            {"admin_write": "2/min"},
        ):
            headers = self.auth_de(self.usuario)

            # 1) três escritas negadas: nenhuma vira 429, porque o throttle
            #    nem chega a ser consultado
            for numero in range(3):
                resposta = self.client.post(
                    CATEGORIAS, {"name": f"N {numero}"}, format="json", **headers
                )
                self.assertEqual(resposta.status_code, 403)

            # 2) promovido no banco, o MESMO usuário tem a cota cheia
            self.usuario.role = Role.ADMIN
            self.usuario.save(update_fields=["role"])

            permitidas = [
                self.client.post(
                    CATEGORIAS, {"name": f"A {numero}"}, format="json", **headers
                ).status_code
                for numero in range(2)
            ]
            self.assertEqual(permitidas, [201, 201])

            # 3) e agora o limite aparece: 3ª escrita do mesmo minuto
            excedente = self.client.post(
                CATEGORIAS, {"name": "B"}, format="json", **headers
            )

        self.assertEqual(excedente.status_code, 429)
        self.assertIn("Retry-After", excedente)
