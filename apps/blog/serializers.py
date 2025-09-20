from rest_framework import serializers
from .models import Blog, BlogCategory


class BlogSerializer(serializers.ModelSerializer):
    class Meta:
        model = Blog
        fields = '__all__'


class BlogCategorySerializer(serializers.ModelSerializer):
    blogs = BlogSerializer(many=True, read_only=True)

    class Meta:
        model = BlogCategory
        fields = '__all__'
