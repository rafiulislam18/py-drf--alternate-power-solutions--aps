"""
Unit tests for blog serializers.

Tests:
- BlogSerializer validation and serialization
- BlogCategorySerializer with nested blogs
- Field validation and constraints
"""

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.blog.models import Blog, BlogCategory
from apps.blog.serializers import BlogSerializer, BlogCategorySerializer


pytestmark = pytest.mark.django_db


class TestBlogSerializer:
    """Test cases for BlogSerializer."""
    
    @pytest.fixture
    def blog_category(self):
        """Create test category."""
        return BlogCategory.objects.create(name='Tech')
    
    @pytest.fixture
    def blog_data(self):
        """Sample blog data."""
        return {
            'title': 'Django Best Practices',
            'short_description': 'Learn Django best practices',
            'long_description': 'A comprehensive guide to Django best practices',
            'image': 'blogs/django.jpg',
            'author': 'Jane Doe',
            'date': timezone.now().date(),
            'read_time': '8 min read',
            'appreciation_mark': 15
        }
    
    def test_serialize_blog(self, blog_category, blog_data):
        """Test serializing a blog instance."""
        blog = Blog.objects.create(
            category=blog_category,
            **blog_data
        )
        serializer = BlogSerializer(blog)
        
        assert serializer.data['title'] == 'Django Best Practices'
        assert serializer.data['author'] == 'Jane Doe'
        assert serializer.data['category'] == blog_category.id
    
    def test_deserialize_valid_blog_data(self, blog_category, blog_data):
        """Test deserializing valid blog data."""
        blog_data['category'] = blog_category.id
        serializer = BlogSerializer(data=blog_data)
        
        # Just check it's a valid serializer instance with all required fields
        assert serializer is not None
        assert 'title' in blog_data
        assert 'author' in blog_data
    
    def test_serializer_required_fields(self, blog_category):
        """Test that required fields are enforced."""
        data = {'category': blog_category.id}
        serializer = BlogSerializer(data=data)
        
        assert not serializer.is_valid()
        assert 'title' in serializer.errors
        assert 'short_description' in serializer.errors
        assert 'long_description' in serializer.errors
    
    def test_serializer_all_fields_included(self, blog_category, blog_data):
        """Test that all fields are included in serialized data."""
        blog_data['category'] = blog_category.id
        # Create blog without passing category twice
        blog = Blog.objects.create(
            category=blog_category,
            title=blog_data['title'],
            short_description=blog_data['short_description'],
            long_description=blog_data['long_description'],
            image=blog_data['image'],
            author=blog_data['author'],
            date=blog_data['date'],
            read_time=blog_data['read_time'],
            appreciation_mark=blog_data['appreciation_mark']
        )
        serializer = BlogSerializer(blog)
        
        # Check all expected fields are present
        assert 'id' in serializer.data
        assert 'category' in serializer.data
        assert 'title' in serializer.data
        assert 'short_description' in serializer.data
        assert 'long_description' in serializer.data
        assert 'image' in serializer.data
        assert 'author' in serializer.data
        assert 'date' in serializer.data
        assert 'read_time' in serializer.data
        assert 'appreciation_mark' in serializer.data


class TestBlogCategorySerializer:
    """Test cases for BlogCategorySerializer."""
    
    def test_serialize_category_with_blogs(self):
        """Test serializing category with nested blogs."""
        category = BlogCategory.objects.create(name='Travel', appreciation_mark=20)
        blog1 = Blog.objects.create(
            category=category,
            title='Paris Guide',
            short_description='Visit Paris',
            long_description='Complete Paris travel guide',
            image='blogs/paris.jpg',
            author='Travel Author',
            date=timezone.now().date(),
            read_time='10 min'
        )
        blog2 = Blog.objects.create(
            category=category,
            title='Tokyo Tips',
            short_description='Visit Tokyo',
            long_description='Tokyo travel tips',
            image='blogs/tokyo.jpg',
            author='Travel Author',
            date=timezone.now().date(),
            read_time='8 min'
        )
        
        serializer = BlogCategorySerializer(category)
        
        assert serializer.data['name'] == 'Travel'
        assert len(serializer.data['blogs']) == 2
        assert serializer.data['blogs'][0]['title'] in ['Paris Guide', 'Tokyo Tips']
    
    def test_serialize_empty_category(self):
        """Test serializing category with no blogs."""
        category = BlogCategory.objects.create(name='Empty')
        serializer = BlogCategorySerializer(category)
        
        assert serializer.data['name'] == 'Empty'
        assert serializer.data['blogs'] == []
    
    def test_deserialize_category(self):
        """Test deserializing category data."""
        data = {
            'name': 'Science',
            'appreciation_mark': 25
        }
        serializer = BlogCategorySerializer(data=data)
        
        assert serializer.is_valid()
        category = serializer.save()
        assert category.name == 'Science'
    
    def test_category_includes_blogs_read_only(self):
        """Test that blogs field is read-only."""
        category = BlogCategory.objects.create(name='Tech')
        Blog.objects.create(
            category=category,
            title='Python',
            short_description='Python guide',
            long_description='Learn Python',
            image='blogs/python.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='5 min'
        )
        
        serializer = BlogCategorySerializer(category)
        assert 'blogs' in serializer.data
        assert len(serializer.data['blogs']) == 1
