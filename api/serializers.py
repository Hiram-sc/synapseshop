from decimal import Decimal

from rest_framework import serializers

from repositories.models import (
    Category,
    Item,
    Pagamento,
    Pedido,
    PedidoItem,
    StatusPagamento,
    User,
)


class UserSerializer(serializers.ModelSerializer):
    """Dados públicos de um usuário logado.

    `password` e `last_login` ficam de fora de propósito: a senha nunca
    retorna na API, nem mesmo como hash.
    """

    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "is_active"]
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    """Credenciais recebidas no endpoint de login.

    `max_length=128` acompanha a convenção do próprio Django (a coluna
    `password` do `AbstractUser` tem 128 caracteres e é onde o hash cabe), e
    `trim_whitespace=False` evita que um espaço no fim da senha que o usuário
    digitou seja apagado silenciosamente.

    O `style` abaixo não é segurança: ele só faz a Browsable API renderizar o
    campo como senha. O que protege o dado de verdade é a resposta nunca
    devolver a senha e o cliente não guardá-la.
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(
        max_length=128,
        style={"input_type": "password"},
        trim_whitespace=False,
    )


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("O nome da categoria é obrigatório.")
        return name


class ItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Item
        fields = [
            "id",
            "name",
            "description",
            "price",
            "category",
            "category_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "category_name", "created_at", "updated_at"]

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("O nome do item é obrigatório.")
        return name

    def validate_price(self, value):
        if value is None or value < Decimal("0.00"):
            raise serializers.ValidationError("O preço não pode ser negativo.")
        return value


class PedidoItemCreateSerializer(serializers.Serializer):
    """Linha recebida no POST do pedido: só o que o cliente decide.

    Preço e nome não vêm daqui de propósito: são resolvidos no servidor a
    partir do catálogo (`services/pedido_service.py`).
    """

    item_id = serializers.IntegerField(min_value=1)
    quantidade = serializers.IntegerField(min_value=1)


class PedidoCreateSerializer(serializers.Serializer):
    """Corpo do `POST /api/v1/pedidos/`.

    `idempotency_key` é obrigatória: é ela que faz um POST repetido devolver o
    mesmo pedido em vez de criar outro.
    """

    idempotency_key = serializers.CharField(max_length=128, allow_blank=False)
    itens = PedidoItemCreateSerializer(many=True, allow_empty=False)


class PedidoItemSerializer(serializers.ModelSerializer):
    """Linha congelada do pedido; `subtotal` é derivado, nunca enviado."""

    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = PedidoItem
        fields = ["item", "nome_item", "quantidade", "preco_unitario", "subtotal"]

    def get_subtotal(self, obj: PedidoItem) -> str:
        return format(obj.preco_unitario * obj.quantidade, "f")


class PedidoSerializer(serializers.ModelSerializer):
    """Leitura do pedido; `total` sai como string (Decimal do DRF)."""

    itens = PedidoItemSerializer(many=True, read_only=True)
    usuario = serializers.CharField(source="usuario.username", read_only=True)

    class Meta:
        model = Pedido
        fields = [
            "id",
            "usuario",
            "status",
            "total",
            "processado_em",
            "idempotency_key",
            "created_at",
            "itens",
        ]
        read_only_fields = fields


class PagamentoCreateSerializer(serializers.Serializer):
    """Corpo do `POST /api/v1/pedidos/{id}/pagamento/`.

    `status` é o desfecho decidido pelo cliente (simulação determinística, sem
    regra por valor); ausente no corpo, vale `APROVADO`. Valor fora de
    `APROVADO`/`RECUSADO` é recusado com 400 pelo `ChoiceField`.
    """

    status = serializers.ChoiceField(
        choices=StatusPagamento.choices,
        default=StatusPagamento.APROVADO,
    )


class PagamentoSerializer(serializers.ModelSerializer):
    """Leitura do pagamento; `pedido` sai como id (já endereçável na URL)."""

    class Meta:
        model = Pagamento
        fields = ["id", "pedido", "status", "created_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Aula 13 - serializers usados apenas para descrever o contrato OpenAPI
# ---------------------------------------------------------------------------
# Nao participam do fluxo de request/response: existem para o drf-spectacular
# documentar as respostas compostas (que nao sao ModelSerializer puros) e o
# formato padrao de erro da API.


class ErrorSerializer(serializers.Serializer):
    """Formato dos erros simples da API (`detail`).

    Erros de validacao (HTTP 400) usam o formato do DRF: um mapa
    `campo -> lista de mensagens`, descrito separadamente.
    """

    detail = serializers.CharField(help_text="Mensagem legível do erro.")
    code = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Código estável opcional para tratamento pelo cliente.",
    )


class LoginResponseSerializer(serializers.Serializer):
    """Resposta do `POST /api/v1/auth/login/`."""

    access = serializers.CharField(help_text="Token JWT de acesso.")
    token_type = serializers.CharField(help_text="Tipo do token (sempre `Bearer`).")
    expires_in = serializers.IntegerField(
        help_text="Validade do token em segundos."
    )
    user = UserSerializer(help_text="Usuário autenticado.")


class PedidoCriadoResponseSerializer(serializers.Serializer):
    """Resposta do `POST /api/v1/pedidos/` (201, 200 e 202)."""

    pedido = PedidoSerializer(help_text="Pedido persistido.")
    evento_publicado = serializers.BooleanField(
        help_text="`true` quando o broker confirmou a publicação do evento."
    )
    detalhe = serializers.CharField(
        required=False,
        help_text="Explica por que o evento não foi publicado (200/202).",
    )


class PagamentoResponseSerializer(serializers.Serializer):
    """Resposta do `POST /api/v1/pedidos/{id}/pagamento/` (201 e 202)."""

    pagamento = PagamentoSerializer(help_text="Pagamento persistido.")
    pedido = PedidoSerializer(help_text="Pedido atualizado.")
    evento_publicado = serializers.BooleanField(
        help_text="`true` quando o broker confirmou a publicação do evento."
    )
    detalhe = serializers.CharField(
        required=False,
        help_text="Explica por que o evento não foi publicado (202).",
    )


class PagamentoJaProcessadoResponseSerializer(serializers.Serializer):
    """Resposta `409` do pagamento já processado."""

    detail = serializers.CharField(help_text="Mensagem do conflito.")
    pagamento = PagamentoSerializer(help_text="Pagamento já existente.")
    pedido = PedidoSerializer(help_text="Pedido associado.")


class AssistRequestSerializer(serializers.Serializer):
    """Corpo do `POST /api/v1/assist` (Aula 14).

    `logs` aceita **string** (um bloco de texto) ou **array** (lista de linhas,
    possivelmente dicts estruturados) conforme o contrato da spec. O
    `JSONField` entrega o valor já desserializado e `validate_logs` restringe a
    esses dois formatos; a normalização para texto fica em `services.prompts`.
    """

    mode = serializers.ChoiceField(
        choices=("summarize", "explain"),
        help_text="Modo da assistente de IA: `summarize` ou `explain`.",
    )
    logs = serializers.JSONField(
        required=True,
        help_text=(
            "Logs a analisar: string única ou lista de linhas "
            "(strings ou objetos com `timestamp`/`level`/`message`)."
        ),
    )
    temperature = serializers.FloatField(default=0.2, min_value=0.0, max_value=1.0)
    max_tokens = serializers.IntegerField(default=256, min_value=1, max_value=4096)

    def validate_logs(self, value):
        if isinstance(value, str):
            if not value.strip():
                raise serializers.ValidationError("Os logs não podem ser vazios.")
            return value
        if isinstance(value, (list, tuple)):
            if len(value) == 0:
                raise serializers.ValidationError("A lista de logs não pode ser vazia.")
            return list(value)
        raise serializers.ValidationError(
            "Envie os logs como texto (string) ou lista de linhas."
        )


class AssistMetaSerializer(serializers.Serializer):
    tokens_prompt = serializers.IntegerField()
    tokens_output = serializers.IntegerField()


class AssistResponseSerializer(serializers.Serializer):
    answer = serializers.CharField()
    meta = AssistMetaSerializer()