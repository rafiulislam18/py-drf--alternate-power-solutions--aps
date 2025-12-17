"""
Unit tests for blog models.

Tests:
- BlogCategory model creation and methods
- Blog model creation and relationships
- Model field validations
"""

import pytest
from django.utils import timezone
from apps.blog.models import Blog, BlogCategory


pytestmark = pytest.mark.django_db


class TestBlogCategory:
    """Test cases for BlogCategory model."""
    
    def test_create_blog_category(self):
        """Test creating a blog category."""
        category = BlogCategory.objects.create(
            name='Technology',
            appreciation_mark=5
        )
        assert category.name == 'Technology'
        assert category.appreciation_mark == 5
    
    def test_blog_category_unique_name(self):
        """Test that category names must be unique."""
        BlogCategory.objects.create(name='News')
        
        with pytest.raises(Exception):  # IntegrityError
            BlogCategory.objects.create(name='News')
    
    def test_blog_category_str_representation(self):
        """Test string representation of category."""
        category = BlogCategory.objects.create(name='Travel')
        assert str(category) == 'Travel'
    
    def test_blog_category_default_appreciation_mark(self):
        """Test default appreciation mark is 0."""
        category = BlogCategory.objects.create(name='Lifestyle')
        assert category.appreciation_mark == 0
    
    def test_blog_category_verbose_name_plural(self):
        """Test verbose name plural."""
        assert BlogCategory._meta.verbose_name_plural == 'Blog Categories'


class TestBlog:
    """Test cases for Blog model."""
    
    @pytest.fixture
    def blog_category(self):
        """Create a test blog category."""
        return BlogCategory.objects.create(name='Tech')
    
    def test_create_blog(self, blog_category):
        """Test creating a blog post."""
        blog = Blog.objects.create(
            category=blog_category,
            title='Django Testing Guide',
            short_description='Learn how to test Django apps',
            long_description='Comprehensive guide to testing Django REST Framework applications',
            image='blogs/test_image.jpg',
            author='John Doe',
            date=timezone.now().date(),
            read_time='5 min read',
            appreciation_mark=10
        )
        assert blog.title == 'Django Testing Guide'
        assert blog.category == blog_category
    
    def test_blog_str_representation(self, blog_category):
        """Test string representation of blog."""
        blog = Blog.objects.create(
            category=blog_category,
            title='Test Blog',
            short_description='Test',
            long_description='Test',
            image='blogs/test.jpg',
            author='Test Author',
            date=timezone.now().date(),
            read_time='3 min read'
        )
        assert str(blog) == 'Test Blog'
    
    def test_blog_foreign_key_relationship(self, blog_category):
        """Test blog is related to category."""
        blog = Blog.objects.create(
            category=blog_category,
            title='Test',
            short_description='Test',
            long_description='Test',
            image='blogs/test.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='2 min'
        )
        assert blog.category.name == 'Tech'
    
    def test_blog_cascading_delete(self, blog_category):
        """Test that blogs are deleted when category is deleted."""
        blog = Blog.objects.create(
            category=blog_category,
            title='Test',
            short_description='Test',
            long_description='Test',
            image='blogs/test.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='2 min'
        )
        blog_id = blog.id
        blog_category.delete()
        
        assert not Blog.objects.filter(id=blog_id).exists()
    
    def test_blog_default_appreciation_mark(self, blog_category):
        """Test default appreciation mark is 0."""
        blog = Blog.objects.create(
            category=blog_category,
            title='Test Blog',
            short_description='Test',
            long_description='Test',
            image='blogs/test.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='2 min'
        )
        assert blog.appreciation_mark == 0
    
    def test_blog_multiple_blogs_same_category(self, blog_category):
        """Test multiple blogs can belong to same category."""
        blog1 = Blog.objects.create(
            category=blog_category,
            title='Blog 1',
            short_description='Test 1',
            long_description='Test 1',
            image='blogs/test1.jpg',
            author='Author 1',
            date=timezone.now().date(),
            read_time='2 min'
        )
        blog2 = Blog.objects.create(
            category=blog_category,
            title='Blog 2',
            short_description='Test 2',
            long_description='Test 2',
            image='blogs/test2.jpg',
            author='Author 2',
            date=timezone.now().date(),
            read_time='3 min'
        )
        
        assert blog_category.blogs.count() == 2
        assert blog1 in blog_category.blogs.all()
        assert blog2 in blog_category.blogs.all()
