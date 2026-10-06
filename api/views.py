from django.http import JsonResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.exceptions import NotFound
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
from services import cache as cache_service
from services import events
from services.auth_service import AuthService, InactiveUser, InvalidCredentials

#: header que diz de onde veio a resposta do catálogo
HEADER_CACHE = "X-Cache"


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


def _ids_dos_itens(categoria: Category) -> list:
    """Ids dos itens de uma categoria.

    Precisa ser consultado **antes** de apagar a categoria: o `CASCADE` leva os
    itens junto e, depois do `DELETE`, não há mais de onde tirar esses ids para
    invalidar o detalhe de cada um.
    """
    return list(categoria.items.values_list("id", flat=True))


class CategoryViewSet(viewsets.ModelViewSet):
    """CRUD de categorias.

    Leitura (`GET`) é pública; escrita exige `role admin` e o limite de
    escrita (30/min por usuário). `?search=` e `?ordering=` funcionam nas
    duas direções da operação, com a mesma paginação dos itens.

    A listagem de categorias **não** é cacheada (o volume é pequeno e a
    invalidação não custaria caro), mas toda escrita aqui invalida o cache de
    `Item`: o `category_name` aparece no detalhe de cada item e a categoria
    também é filtro da listagem de itens.
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

    def perform_create(self, serializer):
        categoria = serializer.save()
        events.publicar(events.CATEGORIA_CRIADA, categoria_id=categoria.id)

    def perform_update(self, serializer):
        categoria = serializer.save()
        events.publicar(
            events.CATEGORIA_ATUALIZADA,
            categoria_id=categoria.id,
            item_ids=_ids_dos_itens(categoria),
        )

    def perform_destroy(self, instance):
        categoria_id = instance.id
        item_ids = _ids_dos_itens(instance)
        instance.delete()
        events.publicar(
            events.CATEGORIA_REMOVIDA,
            categoria_id=categoria_id,
            item_ids=item_ids,
        )


class ItemViewSet(viewsets.ModelViewSet):
    """CRUD de itens do catálogo, com cache-aside nas leituras.

    Mesmas regras de `CategoryViewSet`, somando os filtros `?is_active=` e
    `?category=` - eles são os que sustentam os índices compostos de
    `repositories.models.Item`.

    As duas leituras (`list` e `retrieve`) são cache-aside: o cache do Redis é
    consultado primeiro e, quando não há nada, o PostgreSQL responde e o
    resultado é gravado. A resposta sempre traz `X-Cache: HIT|MISS|BYPASS`, e
    toda escrita publica um evento que invalida o que ficou velho.
    """

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

    # -- leitura com cache --------------------------------------------------
    def list(self, request, *args, **kwargs):
        """Listagem paginada: o `?page=` seguinte não custa consulta nenhuma.

        O que vai para o cache é só o que depende do banco (`count` e
        `results`). `next`/`previous` são remontados a cada requisição, porque
        são URLs absolutas: cacheá-las faria o cache guardar o host que o
        cliente usou, e o link da página 2 responderia errado para quem chega
        por outro nome de máquina.
        """
        digest = cache_service.digest_listagem(
            page=request.query_params.get(self.pagination_class.page_query_param)
            or 1,
            limit=self.paginator.get_page_size(request),
            ordering=request.query_params.get(
                self.filter_backends[1].ordering_param
            ),
            search=request.query_params.get(
                self.filter_backends[0].search_param
            ),
            is_active=request.query_params.get("is_active"),
            category=request.query_params.get("category"),
        )
        payload, estado = cache_service.obter_listagem(
            digest, lambda: self._listagem_sem_cache(request)
        )

        # o `page` é reconstruído nos dois casos (MISS e HIT) para que o envelope
        # saia idêntico ao que a paginação do DRF produziria
        self.paginator.preparar_pagina_cacheada(
            request, payload["count"], request.query_params.get("page") or 1
        )
        resposta = Response(
            {
                "count": payload["count"],
                "next": self.paginator.get_next_link(),
                "previous": self.paginator.get_previous_link(),
                "results": payload["results"],
            }
        )
        resposta[HEADER_CACHE] = estado
        return resposta

    def _listagem_sem_cache(self, request):
        """Caminho tradicional: filtro, paginação e serialização no banco."""
        pagina = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        serializer = self.get_serializer(pagina, many=True)
        return {
            "count": self.paginator.page.paginator.count,
            "results": serializer.data,
        }

    def retrieve(self, request, *args, **kwargs):
        """Detalhe de um item, endereçado pelo id - logo, chave direta no cache.

        `check_object_permissions` não é chamado aqui (o `get_object` do DRF
        chamaria) porque a permissão do projeto é só de view: `ReadOnlyOrIsAdmin`
        não define `has_object_permission`. Se um dia existir permissão por
        objeto, é aqui que ela precisa entrar.
        """
        item_id = kwargs[self.lookup_field]

        def calcular():
            item = self.get_queryset().filter(pk=item_id).first()
            return None if item is None else self.get_serializer(item).data

        payload, estado = cache_service.obter_detalhe(item_id, calcular)
        if payload is None:
            # mesmo status e mesma mensagem do `get_object` do DRF
            raise NotFound()
        resposta = Response(payload)
        resposta[HEADER_CACHE] = estado
        return resposta

    # -- escrita com invalidação -------------------------------------------
    def perform_create(self, serializer):
        item = serializer.save()
        events.publicar(events.ITEM_CRIADO, item_id=item.id)

    def perform_update(self, serializer):
        item = serializer.save()
        events.publicar(events.ITEM_ATUALIZADO, item_id=item.id)

    def perform_destroy(self, instance):
        item_id = instance.id
        instance.delete()
        events.publicar(events.ITEM_REMOVIDO, item_id=item_id)
