from rest_framework import serializers
from apps.core.content_files import validate_image_size
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


# ---------------------------------------------------------------------------
# Dashboard "Site content" editor (admin only).
# ---------------------------------------------------------------------------

class AdminBlogSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Blog
        fields = [
            'id', 'category', 'category_name', 'title', 'short_description',
            'long_description', 'image', 'author', 'date', 'read_time',
            'appreciation_mark',
        ]

    def validate_image(self, value):
        return validate_image_size(value)


class AdminBlogCategorySerializer(serializers.ModelSerializer):
    blog_count = serializers.SerializerMethodField()

    class Meta:
        model = BlogCategory
        fields = ['id', 'name', 'appreciation_mark', 'blog_count']

    def get_blog_count(self, obj):
        annotated = getattr(obj, 'num_blogs', None)
        return annotated if annotated is not None else obj.blogs.count()

    def validate_name(self, value):
        # The DB constraint is case-sensitive; "EV news" and "ev NEWS" would
        # otherwise both be accepted as separate categories.
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('Enter a category name.')
        clash = BlogCategory.objects.filter(name__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError('A category with that name already exists.')
        return value
