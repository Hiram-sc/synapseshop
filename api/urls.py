from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.views import CategoryViewSet, ItemViewSet, LoginView, MeView

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("items", ItemViewSet, basename="item")

# As rotas de autenticação ficam no mesmo `include("api.urls")` do versionamento
# `/api/v1/`, então ficam acessíveis em `/api/v1/auth/...`:
#   POST /api/v1/auth/login/ - credenciais -> token JWT
#   GET  /api/v1/auth/me/    - usuário do token enviado
urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
