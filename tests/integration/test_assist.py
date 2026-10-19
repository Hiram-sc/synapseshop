import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from repositories.models import User, Role
from services import cache as cache_service


@pytest.mark.django_db
class TestAssistIntegration:
    def setup_method(self):
        cache_service.zerar_metricas()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="alice",
            password="senha123",
            role=Role.USER,
        )
        self.admin = User.objects.create_user(
            username="admin",
            password="senha123",
            role=Role.ADMIN,
        )

    def test_assist_requer_auth(self):
        url = reverse("assist")
        resp = self.client.post(url, {"mode": "summarize", "logs": "erro"}, format="json")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_assist_validacao_400(self):
        url = reverse("assist")
        self.client.force_authenticate(self.user)
        resp = self.client.post(url, {"mode": "invalido", "logs": "erro"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_assist_sucesso_mockado(self, monkeypatch):
        from services import llm_service

        def fake_assinar(self, mode, logs, temperature=0.2, max_tokens=256):
            return {
                "answer": "Resumo: erro detectado",
                "meta": {"tokens_prompt": 5, "tokens_output": 7},
                "telemetria": None,
            }

        monkeypatch.setattr(llm_service.LLMService, "assinar_e_resumir", fake_assinar)
        url = reverse("assist")
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            url,
            {"mode": "summarize", "logs": ["erro X", "warn Y"], "temperature": 0.1, "max_tokens": 100},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["answer"] == "Resumo: erro detectado"
        assert resp.data["meta"] == {"tokens_prompt": 5, "tokens_output": 7}

    def test_assist_timeout_504(self, monkeypatch):
        from services import llm_service

        def fake_assinar(self, mode, logs, temperature=0.2, max_tokens=256):
            raise TimeoutError()

        monkeypatch.setattr(llm_service.LLMService, "assinar_e_resumir", fake_assinar)
        url = reverse("assist")
        self.client.force_authenticate(self.user)
        resp = self.client.post(url, {"mode": "summarize", "logs": "erro"}, format="json")
        assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT

    def test_assist_circuit_breaker_503(self, monkeypatch):
        from services import llm_service

        def fake_assinar(self, mode, logs, temperature=0.2, max_tokens=256):
            raise llm_service.CircuitBreakerError()

        monkeypatch.setattr(llm_service.LLMService, "assinar_e_resumir", fake_assinar)
        url = reverse("assist")
        self.client.force_authenticate(self.user)
        resp = self.client.post(url, {"mode": "summarize", "logs": "erro"}, format="json")
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_assist_resposta_invalida_502(self, monkeypatch):
        from services import llm_service

        def fake_assinar(self, mode, logs, temperature=0.2, max_tokens=256):
            raise ValueError()

        monkeypatch.setattr(llm_service.LLMService, "assinar_e_resumir", fake_assinar)
        url = reverse("assist")
        self.client.force_authenticate(self.user)
        resp = self.client.post(url, {"mode": "summarize", "logs": "erro"}, format="json")
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY

    def test_assist_http_429_mapeado_503(self, monkeypatch):
        from services import llm_service

        def fake_assinar(self, mode, logs, temperature=0.2, max_tokens=256):
            raise Exception("http_429:rate")

        monkeypatch.setattr(llm_service.LLMService, "assinar_e_resumir", fake_assinar)
        url = reverse("assist")
        self.client.force_authenticate(self.user)
        resp = self.client.post(url, {"mode": "summarize", "logs": "erro"}, format="json")
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_assist_circuit_breaker_persiste_entre_requests(self, monkeypatch):
        from services import llm_service

        # instância limpa: garante um breaker fechado para o cenário
        monkeypatch.setattr(llm_service, "_instancia_padrao", None)
        svc = llm_service.obter_llm_service()
        svc.circuit.limite_falhas = 1
        svc.max_tentativas = 1

        def fake_chamar_api(self, payload):
            raise Exception("http_500:boom")

        monkeypatch.setattr(llm_service.LLMService, "_chamar_api", fake_chamar_api)
        url = reverse("assist")
        self.client.force_authenticate(self.user)
        corpo = {"mode": "summarize", "logs": "erro", "max_tokens": 10}

        primeira = self.client.post(url, corpo, format="json")
        # a falha do provedor abre o breaker mantido pela instância compartilhada
        assert primeira.status_code == status.HTTP_502_BAD_GATEWAY

        segunda = self.client.post(url, corpo, format="json")
        # a segunda requisição nem chega ao provedor: breaker aberto -> 503
        assert segunda.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
