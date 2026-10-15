from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from api.health import pronto
from api.views import health

urlpatterns = [
    path("health", health, name="health"),
    #: readiness: 200 só quando PostgreSQL, Redis e broker respondem
    path("health/pronto", pronto, name="health_pronto"),
    # Aula 13 - contrato OpenAPI e interfaces interativas:
    #   /api/schema/  - o documento OpenAPI (YAML/JSON)
    #   /api/docs/    - Swagger UI (tentar as chamadas)
    #   /api/redoc/   - ReDoc (leitura)
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    path("api/v1/", include("api.urls")),
]