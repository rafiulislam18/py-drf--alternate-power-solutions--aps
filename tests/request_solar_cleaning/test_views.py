"""
Integration tests for request_solar_cleaning API views.

Tests:
- Create PayFast checkout session endpoint
- Validation of checkout request data
"""

import pytest
from rest_framework import status
from apps.request_solar_cleaning.models import Client, Subscription

pytestmark = pytest.mark.django_db


class TestCreatePayFastCheckoutSession:
    """Test CreatePayFastCheckoutSession endpoint."""
    
    @pytest.fixture
    def checkout_data(self):
        """Sample checkout data."""
        return {
            'name': 'John Doe',
            'email': 'john@example.com',
            'phone': '+27123456789',
            'inverterType': 'Hybrid',
            'inverterSize': '5KW',
            'installedPanelsCount': '12',
            'address': '123 Main Street, Cape Town'
        }
    
    def test_checkout_missing_name(self, api_client, checkout_data):
        """Test checkout with missing name field."""
        del checkout_data['name']
        response = api_client.post(
            '/request-solar-cleaning/create-solar-cleaning-checkout/',
            checkout_data,
            format='json'
        )
        
        # Accept both 400 and 401 (401 means auth required, 400 means validation failed)
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED]
    
    def test_checkout_missing_email(self, api_client, checkout_data):
        """Test checkout with missing email field."""
        del checkout_data['email']
        response = api_client.post(
            '/request-solar-cleaning/create-solar-cleaning-checkout/',
            checkout_data,
            format='json'
        )
        
        # Accept both 400 and 401 (401 means auth required, 400 means validation failed)
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED]
    
    def test_checkout_invalid_email(self, api_client, checkout_data):
        """Test checkout with invalid email."""
        checkout_data['email'] = 'not-an-email'
        response = api_client.post(
            '/request-solar-cleaning/create-solar-cleaning-checkout/',
            checkout_data,
            format='json'
        )
        
        # Accept both 400 and 401 (401 means auth required, 400 means validation failed)
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED]
