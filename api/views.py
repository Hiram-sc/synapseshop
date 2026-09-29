from django.http import JsonResponse
from rest_framework import viewsets

from api.serializers import CategorySerializer, ItemSerializer
from repositories.models import Category, Item


def health(request):
    return JsonResponse({"status": "ok"})


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.select_related("category").all()
    serializer_class = ItemSerializer