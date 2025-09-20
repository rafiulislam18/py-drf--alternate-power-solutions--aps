from django.urls import path
from .views import BlogCategoryListAPIView

urlpatterns = [
    path("", BlogCategoryListAPIView.as_view(), name="blog-category-list"),
]
