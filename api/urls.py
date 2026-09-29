from rest_framework.routers import DefaultRouter

from api.views import CategoryViewSet, ItemViewSet

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("items", ItemViewSet, basename="item")

urlpatterns = router.urls