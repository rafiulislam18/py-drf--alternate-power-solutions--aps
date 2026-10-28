from functools import partial

from django.db.models import Count, Q
from rest_framework.views import APIView
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.core.content_files import delete_unused_files, file_names
from apps.records import audit
from apps.staff.permissions import HasDashboardPerm
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
    permission_classes = [IsAuthenticated, HasDashboardPerm]
    dashboard_section = 'blogs'  # posts and categories alike
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

    def perform_create(self, serializer):
        audit.log_create(self.request.user, serializer.save())


class AdminBlogDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdminBlogSerializer
    queryset = Blog.objects.select_related("category")

    def perform_update(self, serializer):
        before, snap = file_names(serializer.instance, ["image"]), audit.snapshot(serializer.instance)
        instance = serializer.save()
        delete_unused_files(Blog, ["image"], before - file_names(instance, ["image"]))
        audit.log_update(self.request.user, instance, snap)

    def perform_destroy(self, instance):
        instance.trash(self.request.user)  # image kept until it's deleted for good


# Posts in the trash don't count.
_LIVE_POSTS = Count("blogs", filter=Q(blogs__deleted_at__isnull=True))


class AdminBlogCategoryListCreateView(_AdminContentMixin, generics.ListCreateAPIView):
    serializer_class = AdminBlogCategorySerializer
    queryset = BlogCategory.objects.annotate(num_blogs=_LIVE_POSTS).order_by("-appreciation_mark", "name")

    def perform_create(self, serializer):
        audit.log_create(self.request.user, serializer.save())


class AdminBlogCategoryDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdminBlogCategorySerializer
    queryset = BlogCategory.objects.annotate(num_blogs=_LIVE_POSTS)

    def destroy(self, request, *args, **kwargs):
        # A category's posts would disappear with it — make the admin move or
        # delete them first (posts already in the trash don't count).
        category = self.get_object()
        if category.blogs.exists():
            return Response(
                {"detail": "This category still has posts. Move or delete them first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_update(self, serializer):
        snap = audit.snapshot(serializer.instance)
        audit.log_update(self.request.user, serializer.save(), snap)

    def perform_destroy(self, instance):
        instance.trash(self.request.user)
