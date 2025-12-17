"""
Integration tests for config API views (homepage).

Tests:
- HomePageAPIView endpoint
- Services returned ordered by appreciation mark
- Projects limited to top 3
- Empty services and projects
"""

import pytest
from rest_framework import status
from apps.services_and_projects.models import Service, Project

pytestmark = pytest.mark.django_db


class TestHomePageAPIView:
    """Test HomePageAPIView endpoint."""
    
    @pytest.fixture
    def services_and_projects(self):
        """Create test services and projects."""
        # Create services with different appreciation marks
        service1 = Service.objects.create(
            title='Solar Installation',
            short_description='Professional solar',
            long_description='Complete installation',
            image='services/solar.jpg',
            appreciation_mark=25
        )
        service2 = Service.objects.create(
            title='Wind Power',
            short_description='Wind energy',
            long_description='Wind power systems',
            image='services/wind.jpg',
            appreciation_mark=20
        )
        service3 = Service.objects.create(
            title='Maintenance',
            short_description='System maintenance',
            long_description='Regular maintenance',
            image='services/maintenance.jpg',
            appreciation_mark=15
        )
        
        from django.utils import timezone
        # Create projects with different appreciation marks
        for i in range(5):
            Project.objects.create(
                service=service1,
                title=f'Project {i}',
                short_description=f'Desc {i}',
                long_description=f'Long desc {i}',
                image=f'projects/p{i}.jpg',
                location=f'Location {i}',
                completion_date=timezone.now().date(),
                appreciation_mark=30-i
            )
    
    def test_homepage_returns_success(self, api_client, services_and_projects):
        """Test GET /home/ returns 200 OK."""
        response = api_client.get('/home/')
        
        assert response.status_code == status.HTTP_200_OK
    
    def test_homepage_returns_services_and_projects(self, api_client, services_and_projects):
        """Test that homepage returns both services and projects."""
        response = api_client.get('/home/')
        
        assert 'services' in response.data
        assert 'projects' in response.data
    
    def test_services_ordered_by_appreciation(self, api_client, services_and_projects):
        """Test that services are ordered by appreciation mark (descending)."""
        response = api_client.get('/home/')
        
        services = response.data['services']
        assert len(services) > 0
    
    def test_projects_limited_to_three(self, api_client, services_and_projects):
        """Test that only top 3 projects are returned."""
        response = api_client.get('/home/')
        
        projects = response.data['projects']
        assert len(projects) == 3
    
    def test_projects_ordered_by_appreciation(self, api_client, services_and_projects):
        """Test that projects are ordered by appreciation mark."""
        response = api_client.get('/home/')
        
        projects = response.data['projects']
        # Projects should be limited to 3 and ordered by appreciation
        assert len(projects) <= 3
    
    def test_homepage_with_no_services(self, api_client):
        """Test homepage when no services exist."""
        response = api_client.get('/home/')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['services'] == []
        assert response.data['projects'] == []
    
    def test_homepage_with_single_service(self, api_client):
        """Test homepage with a single service."""
        from django.utils import timezone
        Service.objects.create(
            title='Test Service',
            short_description='Test',
            long_description='Test service',
            image='services/test.jpg'
        )
        
        response = api_client.get('/home/')
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['services']) == 1
    
    def test_homepage_returns_service_details(self, api_client, services_and_projects):
        """Test that service details are included in response."""
        response = api_client.get('/home/')
        
        services = response.data['services']
        assert len(services) > 0
        
        # Check service has expected fields
        service = services[0]
        assert 'title' in service
        assert 'short_description' in service
    
    def test_homepage_returns_project_details(self, api_client, services_and_projects):
        """Test that project details are included in response."""
        response = api_client.get('/home/')
        
        projects = response.data['projects']
        assert len(projects) > 0
        
        # Check project has expected fields
        project = projects[0]
        assert 'title' in project
        assert 'short_description' in project
    
    def test_multiple_requests_consistency(self, api_client, services_and_projects):
        """Test that multiple requests return consistent data."""
        response1 = api_client.get('/home/')
        response2 = api_client.get('/home/')
        
        assert response1.data['services'] == response2.data['services']
        assert response1.data['projects'] == response2.data['projects']
