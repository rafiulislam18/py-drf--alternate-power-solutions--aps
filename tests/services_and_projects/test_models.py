"""
Unit tests for services_and_projects models.

Tests:
- Service model creation and relationships
- Project model creation and relationships
- Model field validations
"""

import pytest
from django.utils import timezone
from apps.services_and_projects.models import Service, Project

pytestmark = pytest.mark.django_db


class TestService:
    """Test cases for Service model."""
    
    def test_create_service(self):
        """Test creating a service."""
        service = Service.objects.create(
            title='Solar Installation',
            short_description='Professional solar panel installation',
            long_description='We provide complete solar panel installation services',
            image='services/solar.jpg',
            appreciation_mark=10
        )
        assert service.title == 'Solar Installation'
        assert service.appreciation_mark == 10
    
    def test_service_str_representation(self):
        """Test string representation of service."""
        service = Service.objects.create(
            title='Electrical Work',
            short_description='Electrical installation',
            long_description='Professional electrical services',
            image='services/electrical.jpg'
        )
        assert str(service) == 'Electrical Work'
    
    def test_service_default_appreciation_mark(self):
        """Test default appreciation mark is 0."""
        service = Service.objects.create(
            title='Service',
            short_description='Description',
            long_description='Long description',
            image='services/service.jpg'
        )
        assert service.appreciation_mark == 0
    
    def test_service_with_features(self):
        """Test service with features."""
        features = ['Feature 1', 'Feature 2', 'Feature 3']
        service = Service.objects.create(
            title='Advanced Service',
            short_description='With features',
            long_description='Service with multiple features',
            image='services/advanced.jpg',
            features=features
        )
        assert service.features == features
        assert len(service.features) == 3
    
    def test_service_multiple_services(self):
        """Test multiple services can be created."""
        service1 = Service.objects.create(
            title='Service 1',
            short_description='First service',
            long_description='First service description',
            image='services/s1.jpg'
        )
        service2 = Service.objects.create(
            title='Service 2',
            short_description='Second service',
            long_description='Second service description',
            image='services/s2.jpg'
        )
        
        assert Service.objects.count() == 2
        assert service1.title != service2.title


class TestProject:
    """Test cases for Project model."""
    
    @pytest.fixture
    def service(self):
        """Create a test service."""
        return Service.objects.create(
            title='Solar Installation',
            short_description='Solar panel installation',
            long_description='Complete solar panel installation',
            image='services/solar.jpg',
            appreciation_mark=15
        )
    
    def test_create_project(self, service):
        """Test creating a project."""
        project = Project.objects.create(
            service=service,
            title='Residential Solar Setup',
            short_description='Solar installation for home',
            long_description='Complete residential solar setup',
            image='projects/residential.jpg',
            location='Cape Town',
            completion_date=timezone.now().date(),
            duration='3 months',
            appreciation_mark=20
        )
        assert project.title == 'Residential Solar Setup'
        assert project.service == service
    
    def test_project_str_representation(self, service):
        """Test string representation of project."""
        project = Project.objects.create(
            service=service,
            title='Commercial Project',
            short_description='Commercial solar',
            long_description='Large scale commercial installation',
            image='projects/commercial.jpg',
            location='Johannesburg',
            completion_date=timezone.now().date(),
            duration='6 months'
        )
        expected = f"Commercial Project (Solar Installation)"
        assert str(project) == expected
    
    def test_project_default_appreciation_mark(self, service):
        """Test default appreciation mark is 0."""
        project = Project.objects.create(
            service=service,
            title='Project',
            short_description='Test project',
            long_description='Test project description',
            image='projects/test.jpg',
            location='Location',
            completion_date=timezone.now().date(),
            duration='1 month'
        )
        assert project.appreciation_mark == 0
    
    def test_project_with_multiple_images(self, service):
        """Test project with multiple images."""
        project = Project.objects.create(
            service=service,
            title='Multi Image Project',
            short_description='Project with multiple images',
            long_description='Project showcasing progress',
            image='projects/main.jpg',
            image_2='projects/progress.jpg',
            image_3='projects/final.jpg',
            location='Location',
            completion_date=timezone.now().date(),
            duration='2 months'
        )
        assert project.image == 'projects/main.jpg'
        assert project.image_2 == 'projects/progress.jpg'
        assert project.image_3 == 'projects/final.jpg'
    
    def test_project_with_optional_images(self, service):
        """Test project with optional images."""
        project = Project.objects.create(
            service=service,
            title='Single Image Project',
            short_description='Only main image',
            long_description='Project with one image',
            image='projects/single.jpg',
            location='Location',
            completion_date=timezone.now().date(),
            duration='1 month'
        )
        # ImageField returns empty value, not None, when blank=True
        assert not bool(project.image_2)
        assert not bool(project.image_3)
    
    def test_project_with_features(self, service):
        """Test project with features."""
        features = ['Feature A', 'Feature B', 'Feature C']
        project = Project.objects.create(
            service=service,
            title='Featured Project',
            short_description='Project with features',
            long_description='Project highlighting key features',
            image='projects/featured.jpg',
            location='Location',
            completion_date=timezone.now().date(),
            duration='2 months',
            features=features
        )
        assert project.features == features
    
    def test_project_foreign_key_relationship(self, service):
        """Test project is related to service."""
        project = Project.objects.create(
            service=service,
            title='Project',
            short_description='Test',
            long_description='Test',
            image='projects/test.jpg',
            location='Location',
            completion_date=timezone.now().date(),
            duration='1 month'
        )
        assert project.service.title == 'Solar Installation'
    
    def test_project_cascading_delete(self, service):
        """Test that projects are deleted when service is deleted."""
        project = Project.objects.create(
            service=service,
            title='Project',
            short_description='Test',
            long_description='Test',
            image='projects/test.jpg',
            location='Location',
            completion_date=timezone.now().date(),
            duration='1 month'
        )
        project_id = project.id
        service.delete()
        
        assert not Project.objects.filter(id=project_id).exists()
    
    def test_multiple_projects_same_service(self, service):
        """Test multiple projects for same service."""
        project1 = Project.objects.create(
            service=service,
            title='Project 1',
            short_description='First project',
            long_description='First project description',
            image='projects/p1.jpg',
            location='Location 1',
            completion_date=timezone.now().date(),
            duration='1 month'
        )
        project2 = Project.objects.create(
            service=service,
            title='Project 2',
            short_description='Second project',
            long_description='Second project description',
            image='projects/p2.jpg',
            location='Location 2',
            completion_date=timezone.now().date(),
            duration='2 months'
        )
        
        assert service.projects.count() == 2
        assert project1 in service.projects.all()
        assert project2 in service.projects.all()
