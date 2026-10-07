from django.conf import settings
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
        # a ordenação padrão do modelo é reaproveitada pela paginação da API
        ordering = ["name"]
        indexes = [
            # a listagem do catálogo filtra por categoria e ordena por nome
            # (ordenação padrão do modelo), então o par é consultado junto
            models.Index(fields=["category", "name"], name="ix_item_categoria_nome"),
            # o mesmo vale para o filtro de itens ativos, muito usado na vitrine
            models.Index(fields=["is_active", "name"], name="ix_item_ativo_nome"),
        ]

    def __str__(self):
        return self.name


class StatusPedido(models.TextChoices):
    """Estados de um pedido na Aula 9.

    São dois, e a transição é feita por processos diferentes: a API grava
    `CRIADO` na criação e o worker passa para `CONFIRMADO` quando termina de
    processar o evento `PedidoCriado`. Estados de pagamento ou notificação
    pertencem a etapas futuras e não existem aqui.
    """

    CRIADO = "criado", "Criado"
    CONFIRMADO = "confirmado", "Confirmado"


class Pedido(models.Model):
    """Pedido de compra, na etapa mínima exigida pela Aula 9.

    `total` é sempre calculado no servidor a partir do catálogo no momento da
    criação (nunca confiado pelo cliente), e `processado_em` só passa a ter
    valor quando o worker confirma o consumo do evento - é por ele que se
    observa, pela API, que a mensageria funcionou.

    `idempotency_key` é a chave que o cliente repete para que um POST repetido
    devolva o mesmo pedido em vez de criar outro; a unicidade é composta com
    `usuario` para que duas contas não disputem a mesma chave.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(
        max_length=20,
        choices=StatusPedido.choices,
        default=StatusPedido.CRIADO,
    )
    processado_em = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "idempotency_key"],
                name="uq_pedido_usuario_idempotency",
            ),
        ]

    def __str__(self):
        return f"Pedido #{self.pk} ({self.status})"


class PedidoItem(models.Model):
    """Linha de um pedido, com preço e nome congelados no momento da compra.

    `preco_unitario` e `nome_item` são cópias do catálogo, não referências a
    ele: sem isso, editar o preço ou renomear o item na loja reescreveria o
    histórico de pedidos já feitos. Por isso o vínculo com `Item` usa
    `SET_NULL` - apagar um item do catálogo não pode apagar pedidos que já
    existem, e o nome/preço congelados seguem suficientes para reconstruir a
    linha.
    """

    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name="itens",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedidos_itens",
    )
    quantidade = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    preco_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    nome_item = models.CharField(max_length=200)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantidade}x {self.nome_item}"


class EventoProcessado(models.Model):
    """Registro durável de idempotência no PostgreSQL.

    É a segunda e definitiva camada de deduplicação (a primeira é o Redis, com
    TTL, só como caminho rápido). A unicidade de `(event_type,
    idempotency_key)` é a trava contra corrida entre consumidores: se duas
    instâncias processarem a mesma chave ao mesmo tempo, só uma consegue
    inserir; a outra recebe `IntegrityError` e trata a mensagem como
    duplicada.

    O registro é gravado **depois** que o efeito no pedido aconteceu, nunca
    antes: se o processamento falhar depois de a chave ser escrita, uma
    reentrega seria descartada como duplicata sem que o efeito tivesse
    ocorrido.
    """

    event_id = models.CharField(max_length=64)
    event_type = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eventos_processados",
    )
    processado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event_type", "idempotency_key"],
                name="uq_evento_processado_tipo_chave",
            ),
        ]
        indexes = [
            models.Index(fields=["event_id"], name="ix_evento_processado_event_id"),
            # a limpeza de registros expirados varre por idade
            models.Index(fields=["processado_em"], name="ix_evento_processado_data"),
        ]

    def __str__(self):
        return f"{self.event_type}/{self.idempotency_key}"