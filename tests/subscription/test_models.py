"""
Unit tests for subscription models.

Tests:
- Client model creation and relationships
- Subscription model with PayFast integration
- Multiple subscriptions per client
- Cascading deletes
"""

import pytest
from django.utils import timezone
from apps.subscription.models import Client, Subscription

pytestmark = pytest.mark.django_db


class TestClientModel:
    """Test cases for Client model."""
    
    def test_create_client(self):
        """Test creating a client instance."""
        client = Client.objects.create(
            name='John Doe',
            email='john@example.com',
            phone='+27123456789'
        )
        
        assert client.id is not None
        assert client.name == 'John Doe'
        assert client.email == 'john@example.com'
    
    def test_client_with_note(self):
        """Test creating a client with a note."""
        client = Client.objects.create(
            name='Jane Doe',
            email='jane@example.com',
            phone='+27987654321',
            note='VIP customer'
        )
        
        assert client.note == 'VIP customer'
    
    def test_client_note_optional(self):
        """Test that note field is optional."""
        client = Client.objects.create(
            name='Bob',
            email='bob@example.com',
            phone='+27555555555'
        )
        
        assert client.note is None
    
    def test_client_string_representation(self):
        """Test client string representation."""
        client = Client.objects.create(
            name='Test Client',
            email='test@example.com',
            phone='+27111111111'
        )
        
        assert str(client) == 'Test Client'
    
    def test_client_timestamps(self):
        """Test that timestamps are set automatically."""
        client = Client.objects.create(
            name='Timestamp Test',
            email='time@example.com',
            phone='+27222222222'
        )
        
        assert client.created_at is not None
        assert client.updated_at is not None
    
    def test_multiple_clients(self):
        """Test creating multiple clients."""
        client1 = Client.objects.create(
            name='Client 1',
            email='client1@example.com',
            phone='+27333333333'
        )
        client2 = Client.objects.create(
            name='Client 2',
            email='client2@example.com',
            phone='+27444444444'
        )
        
        assert Client.objects.count() == 2
        assert client1.id != client2.id


class TestSubscriptionModel:
    """Test cases for Subscription model."""
    
    @pytest.fixture
    def client(self):
        """Create a test client."""
        return Client.objects.create(
            name='John',
            email='john@example.com',
            phone='+27123456789'
        )
    
    def test_create_subscription(self, client):
        """Test creating a subscription."""
        subscription = Subscription.objects.create(
            client=client,
            inverter_type='Hybrid',
            address='123 Main St',
            is_active=True,
            subscription_length=12
        )
        
        assert subscription.id is not None
        assert subscription.client == client
        assert subscription.is_active is True
    
    def test_subscription_without_client(self):
        """Test creating subscription without client (SET_NULL)."""
        subscription = Subscription.objects.create(
            inverter_type='Grid-tie',
            address='456 Oak Ave',
            is_active=False
        )
        
        assert subscription.client is None
        assert subscription.inverter_type == 'Grid-tie'
    
    def test_subscription_payfast_fields(self, client):
        """Test subscription with PayFast payment fields."""
        subscription = Subscription.objects.create(
            client=client,
            inverter_type='Hybrid',
            address='789 Pine Rd',
            payfast_token='token123',
            payfast_payment_id='payment456'
        )
        
        assert subscription.payfast_token == 'token123'
        assert subscription.payfast_payment_id == 'payment456'
    
    def test_subscription_call_out_balance(self, client):
        """Test subscription with call out balance."""
        subscription = Subscription.objects.create(
            client=client,
            address='Address',
            call_out_balance=5
        )
        
        assert subscription.call_out_balance == 5
    
    def test_subscription_optional_fields(self, client):
        """Test that optional fields can be null."""
        subscription = Subscription.objects.create(
            client=client,
            address='123 Street'
        )
        
        assert subscription.inverter_type is None
        assert subscription.payfast_token is None
        assert subscription.last_payment_date is None
    
    def test_subscription_timestamps(self, client):
        """Test that timestamps are set automatically."""
        subscription = Subscription.objects.create(
            client=client,
            address='123 Street'
        )
        
        assert subscription.created_at is not None
        assert subscription.updated_at is not None
    
    def test_subscription_last_payment_date(self, client):
        """Test setting last payment date."""
        now = timezone.now()
        subscription = Subscription.objects.create(
            client=client,
            address='123 Street',
            last_payment_date=now
        )
        
        assert subscription.last_payment_date is not None
    
    def test_multiple_subscriptions_per_client(self, client):
        """Test that a client can have multiple subscriptions."""
        sub1 = Subscription.objects.create(
            client=client,
            inverter_type='Hybrid',
            address='Address 1'
        )
        sub2 = Subscription.objects.create(
            client=client,
            inverter_type='Grid-tie',
            address='Address 2'
        )
        
        assert client.subscriptions.count() == 2
        assert sub1 in client.subscriptions.all()
        assert sub2 in client.subscriptions.all()
    
    def test_subscription_string_representation(self, client):
        """Test subscription string representation."""
        subscription = Subscription.objects.create(
            client=client,
            address='456 Oak Ave'
        )
        
        assert str(subscription) == '456 Oak Ave'
    
    def test_client_delete_cascades_to_subscriptions(self, client):
        """Test that deleting a client sets subscriptions to null."""
        subscription = Subscription.objects.create(
            client=client,
            address='123 Street'
        )
        
        client.delete()
        
        subscription.refresh_from_db()
        assert subscription.client is None
    
    def test_subscription_default_values(self, client):
        """Test subscription default field values."""
        subscription = Subscription.objects.create(
            client=client,
            address='123 Street'
        )
        
        assert subscription.is_active is False
        assert subscription.subscription_length == 0
        assert subscription.call_out_balance == 0
