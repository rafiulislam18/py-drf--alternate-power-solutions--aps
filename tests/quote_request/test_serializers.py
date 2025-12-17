"""
Unit tests for quote_request serializers.

Tests:
- QuoteRequestSerializer validation and serialization
- Field validation
- Deserialization
"""

import pytest
from apps.services_and_projects.models import Service
from apps.quote_request.models import QuoteRequest
from apps.quote_request.serializers import QuoteRequestSerializer

pytestmark = pytest.mark.django_db


class TestQuoteRequestSerializer:
    """Test cases for QuoteRequestSerializer."""
    
    @pytest.fixture
    def service(self):
        """Create a test service."""
        return Service.objects.create(
            title='Solar Installation',
            short_description='Solar panel installation',
            long_description='Complete solar panel installation service',
            image='services/solar.jpg'
        )
    
    @pytest.fixture
    def quote_data(self):
        """Sample quote request data."""
        return {
            'name': 'John Doe',
            'phone': '+27123456789',
            'email': 'john@example.com',
            'message': 'Interested in solar installation'
        }
    
    def test_serialize_quote_request(self, service, quote_data):
        """Test serializing a quote request instance."""
        quote = QuoteRequest.objects.create(
            service=service,
            **quote_data
        )
        serializer = QuoteRequestSerializer(quote)
        
        assert serializer.data['name'] == 'John Doe'
        assert serializer.data['email'] == 'john@example.com'
        assert serializer.data['service'] == service.id
    
    def test_deserialize_valid_quote_data(self, service, quote_data):
        """Test deserializing valid quote request data."""
        quote_data['service'] = service.id
        serializer = QuoteRequestSerializer(data=quote_data)
        
        assert serializer.is_valid()
        quote = serializer.save()
        assert quote.name == 'John Doe'
        assert quote.service == service
    
    def test_serializer_required_fields(self, service):
        """Test that required fields are enforced."""
        data = {'service': service.id}
        serializer = QuoteRequestSerializer(data=data)
        
        assert not serializer.is_valid()
        assert 'name' in serializer.errors
        assert 'email' in serializer.errors
        assert 'phone' in serializer.errors
    
    def test_serializer_all_fields_included(self, service, quote_data):
        """Test that all fields are included in serialized data."""
        quote = QuoteRequest.objects.create(service=service, **quote_data)
        serializer = QuoteRequestSerializer(quote)
        
        assert 'id' in serializer.data
        assert 'name' in serializer.data
        assert 'phone' in serializer.data
        assert 'email' in serializer.data
        assert 'service' in serializer.data
        assert 'message' in serializer.data
        assert 'created_at' in serializer.data
        assert 'sent_quote' in serializer.data
    
    def test_invalid_email_format(self, service):
        """Test that invalid email is rejected."""
        data = {
            'name': 'John',
            'phone': '+27123456789',
            'email': 'invalid-email',
            'service': service.id
        }
        serializer = QuoteRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert 'email' in serializer.errors
    
    def test_optional_message_field(self, service):
        """Test that message field is optional."""
        data = {
            'name': 'John',
            'phone': '+27123456789',
            'email': 'john@example.com',
            'service': service.id
        }
        serializer = QuoteRequestSerializer(data=data)
        assert serializer.is_valid()
    
    def test_quote_request_with_message(self, service):
        """Test quote request with message."""
        data = {
            'name': 'Jane',
            'phone': '+27987654321',
            'email': 'jane@example.com',
            'service': service.id,
            'message': 'Please contact me soon'
        }
        serializer = QuoteRequestSerializer(data=data)
        assert serializer.is_valid()
        quote = serializer.save()
        assert quote.message == 'Please contact me soon'
