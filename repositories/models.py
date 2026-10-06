from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models
from decimal import Decimal


class Role(models.TextChoices):
    """Papéis (roles) reconhecidos pela API.

    `ADMIN` administra o catálogo (escrita em itens e categorias);
    `USER` apenas lê o catálogo. A ordenação do `TextChoices` é usada na
    serialização e nos testes, então os valores não devem ser renomeados.
    """

    ADMIN = "admin", "Administrador"
    USER = "user", "Usuário"


class User(AbstractUser):
    """Usuário da aplicação, com o papel que autoriza suas ações.

    A senha nunca é gravada em texto puro: o `AbstractUser` delega ao
    hasher configurado no Django (PBKDF2 por padrão). O campo `role` é a
    única autorização embutida no usuário - `is_staff`/`is_superuser` do
    Django continuam existindo, mas não são usados pela API.
    """

    # papel do usuário, com o menor privilégio como padrão seguro
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
    )

    class Meta:
        # o app Django é o dono do modelo, então a tabela é prefixada pelo app
        db_table = "repositories_user"
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self):
        return self.username


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Item(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="items",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name