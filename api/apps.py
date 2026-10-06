from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        """Registra as regras de invalidação do cache.

        `ready()` roda uma vez por processo, depois que o registry de apps está
        pronto. É o lugar certo para ligar assinantes: quem publica eventos são
        as views, e elas só existem depois deste ponto.

        O import fica dentro do método de propósito - `services.cache_invalidation`
        usa o cache do Django, que só pode ser instanciado depois do setup.
        """
        from services import cache_invalidation

        cache_invalidation.registrar()
