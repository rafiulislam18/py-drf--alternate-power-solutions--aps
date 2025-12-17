"""
Unit tests for request_solar_cleaning models.

Tests:
- Client model creation and relationships
- Subscription model creation and relationships
- Model field validations
"""

import pytest
from django.utils import timezone
from apps.request_solar_cleaning.models import Client, Subscription

pytestmark = pytest.mark.django_db


class TestClient:
    """Test cases for Client model."""
    
    def test_create_client(self):
        """Test creating a client."""
        client = Client.objects.create(
            name='John Doe',
            email='john@example.com',
            phone='+27123456789',
            note='VIP customer'
        )
        assert client.name == 'John Doe'
        assert client.email == 'john@example.com'
    
    def test_client_str_representation(self):
        """Test string representation of client."""
        client = Client.objects.create(
            name='Jane Smith',
            email='jane@example.com',
            phone='+27987654321'
        )
        assert str(client) == 'Jane Smith'
    
    def test_client_created_at_auto_generated(self):
        """Test that created_at is automatically generated."""
        client = Client.objects.create(
            name='User',
            email='user@example.com',
            phone='+27111111111'
        )
        assert client.created_at is not None
    
    def test_client_optional_note(self):
        """Test that note is optional."""
        client = Client.objects.create(
            name='User',
            email='user@example.com',
            phone='+27222222222'
        )
        assert client.note is None
    
    def test_client_with_note(self):
        """Test client with note."""
        note = 'Special instructions here'
        client = Client.objects.create(
            name='User',
            email='user@example.com',
            phone='+27333333333',
            note=note
        )
        assert client.note == note
    
    def test_client_updated_at_changes(self):
        """Test that updated_at changes when client is updated."""
        client = Client.objects.create(
            name='User',
            email='user@example.com',
            phone='+27444444444'
        )
        original_updated_at = client.updated_at
        
        client.name = 'Updated Name'
        client.save()
        
        assert client.updated_at >= original_updated_at


class TestSubscription:
    """Test cases for Subscription model."""
    
    @pytest.fixture
    def client(self):
        """Create a test client."""
        return Client.objects.create(
            name='John Doe',
            email='john@example.com',
            phone='+27123456789'
        )
    
    def test_create_subscription(self, client):
        """Test creating a subscription."""
        subscription = Subscription.objects.create(
            client=client,
            inverter_type='Victron',
            inverter_size='5kW',
            address='123 Main Street'
        )
        assert subscription.client == client
        assert subscription.inverter_type == 'Victron'
    
    def test_subscription_str_representation(self, client):
        """Test string representation of subscription."""
        subscription = Subscription.objects.create(
            client=client,
            address='456 Oak Road'
        )
        assert str(subscription) == '456 Oak Road'
    
    def test_subscription_default_is_active(self, client):
        """Test default is_active value is False."""
        subscription = Subscription.objects.create(client=client)
        assert subscription.is_active is False
    
    def test_subscription_optional_fields(self, client):
        """Test that most fields are optional."""
        subscription = Subscription.objects.create(client=client)
        assert subscription.inverter_type is None
        assert subscription.inverter_size is None
        assert subscription.installed_panels_count is None
    
    def test_subscription_with_payfast_details(self, client):
        """Test subscription with PayFast payment details."""
        subscription = Subscription.objects.create(
            client=client,
            payfast_token='token123',
            payfast_payment_id='payment456'
        )
        assert subscription.payfast_token == 'token123'
        assert subscription.payfast_payment_id == 'payment456'
    
    def test_subscription_foreign_key_relationship(self, client):
        """Test subscription is related to client."""
        subscription = Subscription.objects.create(client=client)
        assert subscription.client.name == 'John Doe'
    
    def test_subscription_client_set_null_on_delete(self):
        """Test that client can be null when client is deleted."""
        client = Client.objects.create(
            name='Test Client',
            email='test@example.com',
            phone='+27555555555'
        )
        subscription = Subscription.objects.create(client=client)
        
        client.delete()
        subscription.refresh_from_db()
        
        assert subscription.client is None
    
    def test_subscription_created_at_auto_generated(self, client):
        """Test that created_at is automatically generated."""
        subscription = Subscription.objects.create(client=client)
        assert subscription.created_at is not None
    
    def test_subscription_multiple_for_same_client(self, client):
        """Test multiple subscriptions for same client."""
        sub1 = Subscription.objects.create(
            client=client,
            address='Address 1'
        )
        sub2 = Subscription.objects.create(
            client=client,
            address='Address 2'
        )
        
        assert client.subscriptions.count() == 2
        assert sub1 in client.subscriptions.all()
        assert sub2 in client.subscriptions.all()
    
    def test_subscription_length(self, client):
        """Test subscription length in months."""
        subscription = Subscription.objects.create(
            client=client,
            subscription_length=12
        )
        assert subscription.subscription_length == 12
    
    def test_subscription_with_panels_count(self, client):
        """Test subscription with installed panels count."""
        subscription = Subscription.objects.create(
            client=client,
            installed_panels_count='20'
        )
        assert subscription.installed_panels_count == '20'
