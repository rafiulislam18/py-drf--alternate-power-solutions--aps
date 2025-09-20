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
