from functools import partial

from django.db.models import Count
from rest_framework.views import APIView
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.core.content_files import delete_unused_files, file_names
from apps.core.permissions import IsDashboardAdmin
from utils.exceptions import custom_exception_handler
from .models import BlogCategory, Blog
from .serializers import (
    AdminBlogCategorySerializer,
    AdminBlogSerializer,
    BlogCategorySerializer,
    BlogSerializer,
)


class BlogCategoryListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        categories = BlogCategory.objects.all().order_by("-appreciation_mark")
        serializer = BlogCategorySerializer(categories, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BlogDetailsAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, blog_id, *args, **kwargs):
        try:
            blog = Blog.objects.get(id=blog_id)

            # Get the category and its top 3 blogs
            category = blog.category
            top_blogs = category.blogs.exclude(id=blog_id).order_by("-appreciation_mark")[:3]

            # Create custom response data
            response_data = BlogSerializer(blog).data
            response_data["category"] = BlogCategorySerializer(category).data
            response_data["category"]["top_blogs"] = BlogSerializer(top_blogs, many=True).data

            return Response(response_data, status=status.HTTP_200_OK)
        except Blog.DoesNotExist:
            return Response(
                {"error": "Blog not found"}, status=status.HTTP_404_NOT_FOUND
            )


# ---------------------------------------------------------------------------
# Dashboard "Site content" editor — admin-only CRUD. Multipart so the cover
# image can be uploaded.
# ---------------------------------------------------------------------------

class _AdminContentMixin:
    permission_classes = [IsAuthenticated, IsDashboardAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    # Content lists are small; return them whole (no settings-level paging).
    pagination_class = None

    def get_exception_handler(self):
        # Keep field names alongside "detail" so the editor can highlight inputs.
        return partial(custom_exception_handler, keep_fields=True)


class AdminBlogListCreateView(_AdminContentMixin, generics.ListCreateAPIView):
    serializer_class = AdminBlogSerializer

    def get_queryset(self):
        qs = Blog.objects.select_related("category").order_by("-date", "-id")
        category = self.request.query_params.get("category")
        if category and category.isdigit():
            qs = qs.filter(category_id=category)
        return qs


class AdminBlogDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdminBlogSerializer
    queryset = Blog.objects.select_related("category")

    def perform_update(self, serializer):
        before = file_names(serializer.instance, ["image"])
        instance = serializer.save()
        delete_unused_files(Blog, ["image"], before - file_names(instance, ["image"]))

    def perform_destroy(self, instance):
        files = file_names(instance, ["image"])
        instance.delete()
        delete_unused_files(Blog, ["image"], files)


class AdminBlogCategoryListCreateView(_AdminContentMixin, generics.ListCreateAPIView):
    serializer_class = AdminBlogCategorySerializer
    queryset = BlogCategory.objects.annotate(num_blogs=Count("blogs")).order_by("-appreciation_mark", "name")


class AdminBlogCategoryDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdminBlogCategorySerializer
    queryset = BlogCategory.objects.annotate(num_blogs=Count("blogs"))

    def destroy(self, request, *args, **kwargs):
        # The FK cascades, so deleting a category with posts would silently
        # delete those posts too — make the admin move or delete them first.
        category = self.get_object()
        if category.blogs.exists():
            return Response(
                {"detail": "This category still has posts. Move or delete them first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)
