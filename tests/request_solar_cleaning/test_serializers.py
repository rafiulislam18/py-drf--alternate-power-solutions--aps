"""
Unit tests for request_solar_cleaning serializers.

Tests:
- CreateCheckoutSessionSerializer validation
- Required field validation
- Email field validation
"""

import pytest
from apps.request_solar_cleaning.serializers import CreateCheckoutSessionSerializer

pytestmark = pytest.mark.django_db


class TestCreateCheckoutSessionSerializer:
    """Test cases for CreateCheckoutSessionSerializer."""
    
    @pytest.fixture
    def valid_data(self):
        """Sample valid checkout data."""
        return {
            'name': 'John Doe',
            'email': 'john@example.com',
            'phone': '+27123456789',
            'inverterType': 'Hybrid',
            'inverterSize': '5KW',
            'installedPanelsCount': '12',
            'address': '123 Main Street'
        }
    
    def test_serialize_valid_checkout_data(self, valid_data):
        """Test serializing valid checkout data."""
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        assert serializer.is_valid()
        assert serializer.validated_data['name'] == 'John Doe'
    
    def test_deserialize_valid_checkout(self, valid_data):
        """Test deserializing valid checkout data."""
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        assert serializer.is_valid()
    
    def test_required_fields_validation(self):
        """Test that required fields are enforced."""
        data = {'address': '123 Street'}
        serializer = CreateCheckoutSessionSerializer(data=data)
        
        assert not serializer.is_valid()
        assert 'name' in serializer.errors
        assert 'email' in serializer.errors
    
    def test_missing_inverter_type(self):
        """Test that inverterType is required."""
        data = {
            'name': 'John',
            'email': 'john@example.com',
            'phone': '+27123456789',
            'inverterSize': '5KW',
            'installedPanelsCount': '12',
            'address': '123 Street'
        }
        serializer = CreateCheckoutSessionSerializer(data=data)
        
        assert not serializer.is_valid()
        assert 'inverterType' in serializer.errors
    
    def test_missing_inverter_size(self):
        """Test that inverterSize is required."""
        data = {
            'name': 'John',
            'email': 'john@example.com',
            'phone': '+27123456789',
            'inverterType': 'Hybrid',
            'installedPanelsCount': '12',
            'address': '123 Street'
        }
        serializer = CreateCheckoutSessionSerializer(data=data)
        
        assert not serializer.is_valid()
        assert 'inverterSize' in serializer.errors
    
    def test_missing_installed_panels_count(self):
        """Test that installedPanelsCount is required."""
        data = {
            'name': 'John',
            'email': 'john@example.com',
            'phone': '+27123456789',
            'inverterType': 'Hybrid',
            'inverterSize': '5KW',
            'address': '123 Street'
        }
        serializer = CreateCheckoutSessionSerializer(data=data)
        
        assert not serializer.is_valid()
        assert 'installedPanelsCount' in serializer.errors
