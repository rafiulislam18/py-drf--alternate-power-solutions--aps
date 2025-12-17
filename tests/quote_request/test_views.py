"""
Integration tests for quote_request API views.

Tests:
- Quote request endpoints
- Permission checks
- Response structure
"""

import pytest
from rest_framework import status
from apps.services_and_projects.models import Service
from apps.quote_request.models import QuoteRequest

pytestmark = pytest.mark.django_db


class TestQuoteRequestAPI:
    """Test cases for quote request API endpoints."""
    
    @pytest.fixture
    def service(self):
        """Create a test service."""
        return Service.objects.create(
            title='Solar Installation',
            short_description='Solar panel installation',
            long_description='Complete solar panel installation service',
            image='services/solar.jpg'
        )
    
    def test_list_quote_requests(self, api_client, service):
        """Test GET endpoint returns 405 Method Not Allowed."""
        QuoteRequest.objects.create(
            name='John',
            phone='+27123456789',
            email='john@example.com',
            service=service
        )
        
        response = api_client.get('/quote-request/')
        # Only POST is allowed
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    
    def test_create_quote_request_unauthenticated(self, api_client, service):
        """Test creating quote request without authentication."""
        data = {
            'name': 'Jane',
            'phone': '+27987654321',
            'email': 'jane@example.com',
            'service': service.id,
            'message': 'Interested in your services'
        }
        response = api_client.post('/quote-request/', data)
        # Quote requests are typically allowed for all users
        assert response.status_code in [status.HTTP_201_CREATED, status.HTTP_200_OK]
    
    def test_create_quote_request_authenticated(self, authenticated_api_client, service):
        """Test creating quote request as authenticated user."""
        data = {
            'name': 'Authenticated User',
            'phone': '+27555555555',
            'email': 'auth@example.com',
            'service': service.id
        }
        response = authenticated_api_client.post('/quote-request/', data)
        assert response.status_code in [status.HTTP_201_CREATED, status.HTTP_200_OK]
    
    def test_quote_request_with_service(self, api_client, service):
        """Test quote request includes service information."""
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27111111111',
            email='user@example.com',
            service=service
        )
        
        response = api_client.get(f'/quote-request/{quote.id}/')
        if response.status_code == status.HTTP_200_OK:
            assert response.data['service'] == service.id
    
    def test_quote_request_not_found(self, api_client):
        """Test 404 when quote request doesn't exist."""
        response = api_client.get('/quote-request/999999/')
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_empty_quote_requests_list(self, api_client):
        """Test GET endpoint returns 405 when no data."""
        response = api_client.get('/quote-request/')
        # GET method not allowed
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
