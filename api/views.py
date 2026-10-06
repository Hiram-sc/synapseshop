from django.http import JsonResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.settings import api_settings as jwt_settings

from api.pagination import CatalogoPagination
from api.permissions import ReadOnlyOrIsAdmin
from api.serializers import (
    CategorySerializer,
    ItemSerializer,
    LoginSerializer,
    UserSerializer,
)
from api.throttling import LoginRateThrottle
from repositories.models import Category, Item
from services.auth_service import AuthService, InactiveUser, InvalidCredentials


def health(request):
    return JsonResponse({"status": "ok"})


class LoginView(APIView):
    """Autentica credenciais e devolve um token JWT de acesso.

    Rota pública por definição - é a porta de entrada - e com o limite mais
    apertado da API (5 tentativas por minuto, por IP), porque é a única rota
    que recebe credenciais de fora sem token.

    Falhas de credencial respondem 401 com mensagem genérica, sem distinguir
    "usuário inexistente" de "senha errada" - assim o login não serve para
    enumerar quais contas existem. Conta desativada responde 403, mas só depois
    que a senha conferiu: quem recebe 403 já provou conhecer a senha, então a
    resposta não vira oráculo de enumeração.
    """

    permission_classes = [AllowAny]
    # a rota de login não valida token nenhum: o cliente ainda não tem um
    authentication_classes = []
    serializer_class = LoginSerializer
    # `throttle_classes` aqui substitui os limites globais de propósito: o
    # login é limitado só pela regra de força bruta, com teto bem menor
    throttle_classes = [LoginRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = AuthService()
        try:
            authenticated = service.login(
                username=serializer.validated_data["username"],
                password=serializer.validated_data["password"],
            )
        except InvalidCredentials:
            return Response(
                {"detail": "Usuário ou senha inválidos."}, status=401
            )
        except InactiveUser:
            return Response(
                {"detail": "Conta desativada. Procure o administrador."},
                status=403,
            )

        user = authenticated.user
        return Response(
            {
                "access": authenticated.access_token,
                "token_type": "Bearer",
                "expires_in": int(
                    jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds()
                ),
                "user": UserSerializer(user).data,
            },
            status=200,
        )


class MeView(GenericAPIView):
    """Devolve o usuário do token enviado na requisição.

    Existe para o cliente (e para os testes) confirmar quem está autenticado
    e qual role o token carrega, sem precisar decodificá-lo à mão.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def get(self, request):
        return Response(self.serializer_class(request.user).data)



class CategoryViewSet(viewsets.ModelViewSet):
    """CRUD de categorias.

    Leitura (`GET`) é pública; escrita exige `role admin` e o limite de
    escrita (30/min por usuário). `?search=` e `?ordering=` funcionam nas
    duas direções da operação, com a mesma paginação dos itens.
    """

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [ReadOnlyOrIsAdmin]
    pagination_class = CatalogoPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    # escopo lido pelo DRF; `AdminWriteThrottle` só o conta nas escritas
    throttle_scope = "admin_write"


class ItemViewSet(viewsets.ModelViewSet):

    queryset = Item.objects.select_related("category").all()
    serializer_class = ItemSerializer
    permission_classes = [ReadOnlyOrIsAdmin]
    pagination_class = CatalogoPagination
    filter_backends = [
        filters.SearchFilter,
        filters.OrderingFilter,
        DjangoFilterBackend,
    ]
    filterset_fields = ["is_active", "category"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "price", "created_at", "updated_at"]
    throttle_scope = "admin_write"
