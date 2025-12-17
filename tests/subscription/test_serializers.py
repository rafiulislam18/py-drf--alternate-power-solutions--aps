"""
Unit tests for subscription serializers.

Tests:
- CreateCheckoutSessionSerializer validation
- Required field validation
- Email field validation
"""

import pytest
from apps.subscription.serializers import CreateCheckoutSessionSerializer

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
            'address': '123 Main Street, Cape Town'
        }
    
    def test_serialize_valid_data(self, valid_data):
        """Test serializing valid data."""
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        assert serializer.is_valid()
        assert serializer.validated_data['name'] == 'John Doe'
        assert serializer.validated_data['email'] == 'john@example.com'
    
    def test_missing_name_field(self, valid_data):
        """Test that name field is required."""
        del valid_data['name']
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'name' in serializer.errors
    
    def test_missing_email_field(self, valid_data):
        """Test that email field is required."""
        del valid_data['email']
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'email' in serializer.errors
    
    def test_missing_phone_field(self, valid_data):
        """Test that phone field is required."""
        del valid_data['phone']
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'phone' in serializer.errors
    
    def test_missing_inverter_type_field(self, valid_data):
        """Test that inverterType field is required."""
        del valid_data['inverterType']
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'inverterType' in serializer.errors
    
    def test_missing_address_field(self, valid_data):
        """Test that address field is required."""
        del valid_data['address']
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'address' in serializer.errors
    
    def test_invalid_email_format(self, valid_data):
        """Test that invalid email is rejected."""
        valid_data['email'] = 'not-an-email'
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'email' in serializer.errors
    
    def test_name_max_length(self, valid_data):
        """Test name field max length."""
        valid_data['name'] = 'x' * 256
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'name' in serializer.errors
    
    def test_email_max_length(self, valid_data):
        """Test email field max length."""
        # Email max_length is 320, so 310 chars should pass
        valid_data['email'] = 'x' * 300 + '@example.com'
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        # This should be valid since it's 310 chars and max is 320
        assert serializer.is_valid()
    
    def test_phone_max_length(self, valid_data):
        """Test phone field max length."""
        valid_data['phone'] = 'x' * 21
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'phone' in serializer.errors
    
    def test_inverter_type_max_length(self, valid_data):
        """Test inverterType field max length."""
        valid_data['inverterType'] = 'x' * 256
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'inverterType' in serializer.errors
    
    def test_address_max_length(self, valid_data):
        """Test address field max length."""
        valid_data['address'] = 'x' * 511
        serializer = CreateCheckoutSessionSerializer(data=valid_data)
        
        assert not serializer.is_valid()
        assert 'address' in serializer.errors
