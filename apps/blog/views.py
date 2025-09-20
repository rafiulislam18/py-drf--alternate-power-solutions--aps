from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .models import BlogCategory, Blog
from .serializers import BlogCategorySerializer, BlogSerializer


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
