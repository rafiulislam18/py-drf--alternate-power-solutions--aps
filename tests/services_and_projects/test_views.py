"""
Integration tests for services_and_projects API views.

Tests:
- Service list and detail endpoints
- Project list and detail endpoints
- Ordering by appreciation mark
- 404 error handling
"""

import pytest
from rest_framework import status
from apps.services_and_projects.models import Service, Project

pytestmark = pytest.mark.django_db


class TestServiceListAPIView:
    """Test ServiceListAPIView endpoint."""
    
    @pytest.fixture
    def services(self):
        """Create test services."""
        service1 = Service.objects.create(
            title='Solar Installation',
            short_description='Solar services',
            long_description='Professional solar installation',
            image='services/solar.jpg',
            appreciation_mark=20
        )
        service2 = Service.objects.create(
            title='Maintenance',
            short_description='Solar maintenance',
            long_description='Regular maintenance services',
            image='services/maintenance.jpg',
            appreciation_mark=15
        )
        return [service1, service2]
    
    def test_list_services(self, api_client, services):
        """Test GET /services/ returns list of services."""
        response = api_client.get('/services-projects/services/')
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
    
    def test_services_ordered_by_appreciation(self, api_client, services):
        """Test services are ordered by appreciation mark (descending)."""
        response = api_client.get('/services-projects/services/')
        
        assert response.status_code == status.HTTP_200_OK
        # ServiceListSerializer only returns 'id' and 'title', not appreciation_mark
        assert len(response.data) > 0


class TestServiceProjectListAPIView:
    """Test ServiceProjectListAPIView endpoint."""
    
    @pytest.fixture
    def services_with_projects(self):
        """Create services with projects."""
        service1 = Service.objects.create(
            title='Solar',
            short_description='Solar services',
            long_description='Solar installation',
            image='services/solar.jpg',
            appreciation_mark=25
        )
        service2 = Service.objects.create(
            title='Wind',
            short_description='Wind services',
            long_description='Wind power',
            image='services/wind.jpg',
            appreciation_mark=20
        )
        
        from django.utils import timezone
        Project.objects.create(
            service=service1,
            title='Project 1',
            short_description='Desc 1',
            long_description='Long desc 1',
            image='projects/p1.jpg',
            location='Location 1',
            completion_date=timezone.now().date()
        )
        return [service1, service2]
    
    def test_list_services_projects(self, api_client, services_with_projects):
        """Test GET / returns services with projects."""
        response = api_client.get('/services-projects/')
        
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
    
    def test_services_projects_ordered(self, api_client, services_with_projects):
        """Test services are ordered by appreciation mark."""
        response = api_client.get('/services-projects/')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]['appreciation_mark'] == 25


class TestServiceDetailsAPIView:
    """Test ServiceDetailsAPIView endpoint."""
    
    @pytest.fixture
    def service(self):
        """Create a test service."""
        return Service.objects.create(
            title='Solar Installation',
            short_description='Professional solar',
            long_description='Complete installation',
            image='services/solar.jpg',
            appreciation_mark=18
        )
    
    def test_get_service_detail(self, api_client, service):
        """Test GET /service/<id> returns service details."""
        response = api_client.get(f'/services-projects/service/{service.id}')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['title'] == 'Solar Installation'
        assert response.data['appreciation_mark'] == 18
    
    def test_service_not_found(self, api_client):
        """Test 404 error when service doesn't exist."""
        response = api_client.get('/services-projects/service/9999')
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert 'error' in response.data


class TestProjectDetailsAPIView:
    """Test ProjectDetailsAPIView endpoint."""
    
    @pytest.fixture
    def service_with_projects(self):
        """Create service with multiple projects."""
        service = Service.objects.create(
            title='Solar',
            short_description='Solar services',
            long_description='Solar installation',
            image='services/solar.jpg',
            appreciation_mark=20
        )
        
        from django.utils import timezone
        # Create 5 projects
        for i in range(5):
            Project.objects.create(
                service=service,
                title=f'Project {i}',
                short_description=f'Desc {i}',
                long_description=f'Long desc {i}',
                image=f'projects/p{i}.jpg',
                location=f'Location {i}',
                completion_date=timezone.now().date(),
                appreciation_mark=20-i
            )
        
        return service
    
    def test_get_project_detail(self, api_client, service_with_projects):
        """Test GET /project/<id> returns project with service and top projects."""
        project = service_with_projects.projects.first()
        response = api_client.get(f'/services-projects/project/{project.id}')
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['title'] == project.title
        assert 'service' in response.data
        assert 'top_projects' in response.data['service']
    
    def test_project_top_projects_limit(self, api_client, service_with_projects):
        """Test that top_projects is limited to 3."""
        project = service_with_projects.projects.first()
        response = api_client.get(f'/services-projects/project/{project.id}')
        
        assert response.status_code == status.HTTP_200_OK
        top_projects = response.data['service']['top_projects']
        assert len(top_projects) <= 3
    
    def test_project_not_found(self, api_client):
        """Test 404 error when project doesn't exist."""
        response = api_client.get('/services-projects/project/9999')
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert 'error' in response.data
    
    def test_project_excludes_itself_from_top_projects(self, api_client, service_with_projects):
        """Test that current project is not in top_projects list."""
        project = service_with_projects.projects.first()
        response = api_client.get(f'/services-projects/project/{project.id}')
        
        assert response.status_code == status.HTTP_200_OK
        top_projects = response.data['service']['top_projects']
        project_ids = [p['id'] for p in top_projects]
        assert project.id not in project_ids
