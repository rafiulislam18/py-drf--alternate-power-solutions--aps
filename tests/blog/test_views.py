"""
Integration tests for blog API views.

Tests:
- BlogCategoryListAPIView - GET list of categories
- BlogDetailsAPIView - GET specific blog with related data
- Permission checks (AllowAny)
- Response structure validation
"""

import pytest
from django.utils import timezone
from rest_framework import status
from apps.blog.models import Blog, BlogCategory


pytestmark = pytest.mark.django_db


class TestBlogCategoryListAPIView:
    """Test cases for BlogCategoryListAPIView."""
    
    def test_get_all_categories(self, api_client):
        """Test retrieving all blog categories."""
        category1 = BlogCategory.objects.create(name='Tech', appreciation_mark=5)
        category2 = BlogCategory.objects.create(name='Travel', appreciation_mark=3)
        
        response = api_client.get('/blogs/')
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
    
    def test_categories_ordered_by_appreciation_mark(self, api_client):
        """Test categories are ordered by appreciation mark descending."""
        category1 = BlogCategory.objects.create(name='Low', appreciation_mark=1)
        category2 = BlogCategory.objects.create(name='High', appreciation_mark=10)
        category3 = BlogCategory.objects.create(name='Medium', appreciation_mark=5)
        
        response = api_client.get('/blogs/')
        
        assert response.status_code == status.HTTP_200_OK
        names = [cat['name'] for cat in response.data]
        assert names == ['High', 'Medium', 'Low']
    
    def test_categories_include_blogs(self, api_client):
        """Test response includes nested blogs."""
        category = BlogCategory.objects.create(name='Tech')
        blog = Blog.objects.create(
            category=category,
            title='Django Tips',
            short_description='Tips',
            long_description='Tips for Django',
            image='blogs/django.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='5 min'
        )
        
        response = api_client.get('/blogs/')
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data[0]['blogs']) == 1
        assert response.data[0]['blogs'][0]['title'] == 'Django Tips'
    
    def test_empty_categories_list(self, api_client):
        """Test response when no categories exist."""
        response = api_client.get('/blogs/')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []
    
    def test_allow_any_permission(self, api_client):
        """Test that unauthenticated users can access."""
        BlogCategory.objects.create(name='Public')
        
        response = api_client.get('/blogs/')
        
        assert response.status_code == status.HTTP_200_OK


class TestBlogDetailsAPIView:
    """Test cases for BlogDetailsAPIView."""
    
    @pytest.fixture
    def blog_with_category(self):
        """Create test blog with category."""
        category = BlogCategory.objects.create(name='Travel', appreciation_mark=5)
        blog = Blog.objects.create(
            category=category,
            title='Main Blog',
            short_description='This is main',
            long_description='This is the main blog',
            image='blogs/main.jpg',
            author='John Doe',
            date=timezone.now().date(),
            read_time='5 min',
            appreciation_mark=20
        )
        return blog
    
    def test_get_blog_details(self, api_client, blog_with_category):
        """Test retrieving blog details."""
        response = api_client.get(f'/blogs/{blog_with_category.id}')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['title'] == 'Main Blog'
        assert response.data['author'] == 'John Doe'
    
    def test_blog_details_include_category(self, api_client, blog_with_category):
        """Test response includes category information."""
        response = api_client.get(f'/blogs/{blog_with_category.id}')
        
        assert response.status_code == status.HTTP_200_OK
        assert 'category' in response.data
        assert response.data['category']['name'] == 'Travel'
    
    def test_blog_details_include_top_related_blogs(self, api_client):
        """Test response includes top 3 related blogs from same category."""
        category = BlogCategory.objects.create(name='Tech')
        main_blog = Blog.objects.create(
            category=category,
            title='Main Blog',
            short_description='Main',
            long_description='Main blog',
            image='blogs/main.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='5 min',
            appreciation_mark=50
        )
        
        # Create related blogs
        blog1 = Blog.objects.create(
            category=category,
            title='Related 1',
            short_description='Related',
            long_description='Related blog 1',
            image='blogs/related1.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='3 min',
            appreciation_mark=30
        )
        blog2 = Blog.objects.create(
            category=category,
            title='Related 2',
            short_description='Related',
            long_description='Related blog 2',
            image='blogs/related2.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='4 min',
            appreciation_mark=25
        )
        blog3 = Blog.objects.create(
            category=category,
            title='Related 3',
            short_description='Related',
            long_description='Related blog 3',
            image='blogs/related3.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='6 min',
            appreciation_mark=20
        )
        
        response = api_client.get(f'/blogs/{main_blog.id}')
        
        assert response.status_code == status.HTTP_200_OK
        top_blogs = response.data['category']['top_blogs']
        assert len(top_blogs) == 3
        # Should be ordered by appreciation_mark
        assert top_blogs[0]['appreciation_mark'] >= top_blogs[1]['appreciation_mark']
    
    def test_blog_not_included_in_related(self, api_client):
        """Test that the main blog is not included in related blogs."""
        category = BlogCategory.objects.create(name='Tech')
        blog = Blog.objects.create(
            category=category,
            title='Main',
            short_description='Main',
            long_description='Main blog',
            image='blogs/main.jpg',
            author='Author',
            date=timezone.now().date(),
            read_time='5 min',
            appreciation_mark=100
        )
        
        response = api_client.get(f'/blogs/{blog.id}')
        
        top_blogs = response.data['category']['top_blogs']
        assert blog.id not in [b['id'] for b in top_blogs]
    
    def test_blog_not_found(self, api_client):
        """Test 404 when blog doesn't exist."""
        response = api_client.get('/blogs/999999')
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert 'error' in response.data
    
    def test_allow_any_permission_for_details(self, api_client, blog_with_category):
        """Test that unauthenticated users can access blog details."""
        response = api_client.get(f'/blogs/{blog_with_category.id}')
        
        assert response.status_code == status.HTTP_200_OK
