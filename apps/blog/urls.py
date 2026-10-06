from django.urls import path
from .views import (
    AdminBlogCategoryDetailView,
    AdminBlogCategoryListCreateView,
    AdminBlogDetailView,
    AdminBlogListCreateView,
    BlogCategoryListAPIView,
    BlogDetailsAPIView,
)

urlpatterns = [
    path("", BlogCategoryListAPIView.as_view(), name="blog-category-list"),
    path('<int:blog_id>', BlogDetailsAPIView.as_view(), name='blog-detail'),

    # Dashboard "Site content" editor (admin only)
    path('admin/posts/', AdminBlogListCreateView.as_view(), name='admin-blog-list'),
    path('admin/posts/<int:pk>/', AdminBlogDetailView.as_view(), name='admin-blog-detail'),
    path('admin/categories/', AdminBlogCategoryListCreateView.as_view(), name='admin-blog-category-list'),
    path('admin/categories/<int:pk>/', AdminBlogCategoryDetailView.as_view(), name='admin-blog-category-detail'),
]
