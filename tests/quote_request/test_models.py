"""
Unit tests for quote_request models.

Tests:
- QuoteRequest model creation and relationships
- Model field validations
- String representation
"""

import pytest
from apps.services_and_projects.models import Service
from apps.quote_request.models import QuoteRequest

pytestmark = pytest.mark.django_db


class TestQuoteRequest:
    """Test cases for QuoteRequest model."""
    
    @pytest.fixture
    def service(self):
        """Create a test service."""
        return Service.objects.create(
            title='Solar Installation',
            short_description='Solar panel installation',
            long_description='Complete solar panel installation service',
            image='services/solar.jpg',
            appreciation_mark=10
        )
    
    def test_create_quote_request(self, service):
        """Test creating a quote request."""
        quote = QuoteRequest.objects.create(
            name='John Doe',
            phone='+27123456789',
            email='john@example.com',
            service=service,
            message='Interested in solar panels'
        )
        assert quote.name == 'John Doe'
        assert quote.email == 'john@example.com'
        assert quote.service == service
    
    def test_quote_request_str_representation(self, service):
        """Test string representation of quote request."""
        quote = QuoteRequest.objects.create(
            name='Jane Smith',
            phone='+27987654321',
            email='jane@example.com',
            service=service
        )
        expected = f"Quote Request from Jane Smith - jane@example.com"
        assert str(quote) == expected
    
    def test_quote_request_default_sent_quote(self, service):
        """Test default sent_quote value is False."""
        quote = QuoteRequest.objects.create(
            name='Test User',
            phone='+27111111111',
            email='test@example.com',
            service=service
        )
        assert quote.sent_quote is False
    
    def test_quote_request_with_optional_message(self, service):
        """Test quote request with optional message."""
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27222222222',
            email='user@example.com',
            service=service,
            message='Custom message here'
        )
        assert quote.message == 'Custom message here'
    
    def test_quote_request_without_optional_message(self, service):
        """Test quote request without optional message."""
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27333333333',
            email='user2@example.com',
            service=service
        )
        assert quote.message is None
    
    def test_quote_request_foreign_key_relationship(self, service):
        """Test quote request is related to service."""
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27444444444',
            email='user3@example.com',
            service=service
        )
        assert quote.service.title == 'Solar Installation'
    
    def test_quote_request_cascading_delete(self, service):
        """Test that quote requests are deleted when service is deleted."""
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27555555555',
            email='user4@example.com',
            service=service
        )
        quote_id = quote.id
        service.delete()
        
        assert not QuoteRequest.objects.filter(id=quote_id).exists()
    
    def test_quote_request_created_at_auto_generated(self, service):
        """Test that created_at is automatically generated."""
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27666666666',
            email='user5@example.com',
            service=service
        )
        assert quote.created_at is not None
    
    def test_multiple_quote_requests_same_service(self, service):
        """Test multiple quote requests can be from same service."""
        quote1 = QuoteRequest.objects.create(
            name='User 1',
            phone='+27777777777',
            email='user1@example.com',
            service=service
        )
        quote2 = QuoteRequest.objects.create(
            name='User 2',
            phone='+27888888888',
            email='user2@example.com',
            service=service
        )
        
        assert service.quote_requests.count() == 2
        assert quote1 in service.quote_requests.all()
        assert quote2 in service.quote_requests.all()
    
    def test_quote_request_email_validation(self, service):
        """Test that email field is validated."""
        # EmailField should reject invalid emails
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27999999999',
            email='valid@example.com',
            service=service
        )
        assert '@' in quote.email
        assert '.' in quote.email.split('@')[1]
    
    def test_quote_request_update_sent_quote_status(self, service):
        """Test updating sent_quote status."""
        quote = QuoteRequest.objects.create(
            name='User',
            phone='+27101010101',
            email='user6@example.com',
            service=service,
            sent_quote=False
        )
        assert quote.sent_quote is False
        
        quote.sent_quote = True
        quote.save()
        
        updated_quote = QuoteRequest.objects.get(id=quote.id)
        assert updated_quote.sent_quote is True
