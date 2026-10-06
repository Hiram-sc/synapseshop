from decimal import Decimal

from rest_framework import serializers

from repositories.models import Category, Item, User


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