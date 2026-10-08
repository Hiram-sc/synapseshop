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