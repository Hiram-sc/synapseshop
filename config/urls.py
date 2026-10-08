from django.urls import include, path

from api.health import pronto
from api.views import health

urlpatterns = [
    path("health", health, name="health"),
    #: readiness: 200 só quando PostgreSQL, Redis e broker respondem
    path("health/pronto", pronto, name="health_pronto"),
    path("api/v1/", include("api.urls")),
]