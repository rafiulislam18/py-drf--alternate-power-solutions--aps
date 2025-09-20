from django.urls import path
from .views import BlogCategoryListAPIView, BlogDetailsAPIView

urlpatterns = [
    path("", BlogCategoryListAPIView.as_view(), name="blog-category-list"),
    path('<int:blog_id>/', BlogDetailsAPIView.as_view(), name='blog-detail'),
]
