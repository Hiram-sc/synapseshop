"""Permissões da API - a parte de **autorização**.

A distinção é a seguinte:

* **autenticação** (`api/throttling.py` e o `JWTAuthentication` configurado em
  `config/settings.py`) responde "quem é o usuário?";
* **autorização** (este módulo) responde "esse usuário pode executar esta
  operação?".

O `JWTAuthentication` do DRF já garante a separação dos status: quando não há
token, ou o token é inválido/expirado, ele recusa com **401 Unauthorized**; a
permissão só é consultada depois disso, e um usuário autenticado sem o papel
exigido recebe **403 Forbidden**. Por isso nenhuma permissão precisa tratar
401 - elas dizem apenas "pode ou não pode".
"""

from __future__ import annotations

from rest_framework import permissions

from repositories.models import Role


class IsAdmin(permissions.BasePermission):
    """Exige usuário autenticado **e** com a role `admin`.

    * sem token (ou token inválido): o DRF já devolveu 401;
    * com token válido, mas role `user`: 403.
    """

    message = (
        "Acesso restrito a administradores. "
        "Esta operação exige a role 'admin'."
    )

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            # nunca acontece quando a autenticação falhou: o DRF devolve 401
            # antes de consultar permissões. Fica aqui como trava explícita.
            return False
        return user.role == Role.ADMIN


class ReadOnlyOrIsAdmin(permissions.BasePermission):
    """Leitura pública, escrita exclusiva de `admin`.

    Reproduz o domínio do SynapseShop: a vitrine (loja) lista o catálogo sem
    autenticação, enquanto o painel administrativo publica, altera e remove
    itens e categorias. `GET`/`HEAD`/`OPTIONS` ficam abertos; qualquer outro
    verbo exige `role == admin`.

    O 403 sai com a mensagem padrão do DRF ("você não tem permissão para
    executar essa ação"), e não com a de `IsAdmin`. É deliberado: o cliente já
    sabe que precisa de admin porque é o dono da tela; quem estiver sondando
    a API não ganha de graça a confirmação de que a rota existe e qual é a regra.
    """

    def has_permission(self, request, view) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return IsAdmin().has_permission(request, view)


class DonoOuAdmin(permissions.BasePermission):
    """Usuário autenticado; o dono do pedido ou um admin enxerga o registro.

    A filtragem por dono acontece no `get_queryset` (quem não é dono nem admin
    recebe 404, sem confirmar que o id existe); esta permissão é a segunda
    trava, por objeto, caso o queryset algum dia seja alargado.
    """

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if getattr(user, "role", None) == Role.ADMIN:
            return True
        return getattr(obj, "usuario_id", None) == getattr(user, "id", None)
