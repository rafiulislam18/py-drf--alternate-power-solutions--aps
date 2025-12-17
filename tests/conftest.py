"""
Pytest configuration and shared fixtures for the entire test suite.

This module provides:
- API client fixtures for testing DRF endpoints
- User fixtures (authenticated users, admin users, staff users)
- Database and transaction management fixtures
- Mock and factory fixtures for common testing patterns
"""

import pytest
from django.contrib.auth.models import User, Group, Permission
from django.test import Client
from rest_framework.test import APIClient
from faker import Faker
import factory
from factory.django import DjangoModelFactory


# ============================================================================
# Faker Instance
# ============================================================================

fake = Faker()


# ============================================================================
# Factories for Common Models
# ============================================================================

class UserFactory(DjangoModelFactory):
    """Factory for creating test users."""
    
    class Meta:
        model = User
    
    username = factory.Sequence(lambda n: f'user_{n}')
    email = factory.Faker('email')
    first_name = factory.Faker('first_name')
    last_name = factory.Faker('last_name')
    is_active = True
    is_staff = False
    is_superuser = False
    
    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        """Set password after user creation."""
        if create:
            obj.set_password('test_password_123')
            obj.save()


class AdminUserFactory(UserFactory):
    """Factory for creating admin/superuser accounts."""
    is_staff = True
    is_superuser = True


class StaffUserFactory(UserFactory):
    """Factory for creating staff user accounts."""
    is_staff = True
    is_superuser = False


class GroupFactory(DjangoModelFactory):
    """Factory for creating user groups."""
    
    class Meta:
        model = Group
    
    name = factory.Sequence(lambda n: f'group_{n}')


# ============================================================================
# Database Fixtures
# ============================================================================

@pytest.fixture
def db_setup(db):
    """
    Fixture to ensure database is properly set up before each test.
    Uses pytest-django's db fixture.
    
    Usage:
        def test_something(db_setup):
            pass
    """
    return db


@pytest.fixture
def transactional_db_setup(transactional_db):
    """
    Fixture for tests that need transaction support.
    
    Usage:
        def test_something(transactional_db_setup):
            pass
    """
    return transactional_db


# ============================================================================
# User Fixtures
# ============================================================================

@pytest.fixture
def user(db):
    """
    Create a standard authenticated user.
    
    Returns:
        User: A regular user with username='testuser', password='test_password_123'
    
    Usage:
        def test_user_login(user):
            assert user.username == 'testuser'
    """
    return UserFactory(username='testuser')


@pytest.fixture
def admin_user(db):
    """
    Create a superuser/admin account.
    
    Returns:
        User: Admin user with is_staff=True, is_superuser=True
    
    Usage:
        def test_admin_access(admin_user):
            assert admin_user.is_superuser
    """
    return AdminUserFactory(username='admin')


@pytest.fixture
def staff_user(db):
    """
    Create a staff user account.
    
    Returns:
        User: Staff user with is_staff=True, is_superuser=False
    
    Usage:
        def test_staff_access(staff_user):
            assert staff_user.is_staff and not staff_user.is_superuser
    """
    return StaffUserFactory(username='staff')


@pytest.fixture
def multiple_users(db):
    """
    Create multiple authenticated users.
    
    Returns:
        dict: Dictionary with keys 'user1', 'user2', 'user3'
    
    Usage:
        def test_multiple_users(multiple_users):
            user1 = multiple_users['user1']
            user2 = multiple_users['user2']
    """
    return {
        'user1': UserFactory(username='user1'),
        'user2': UserFactory(username='user2'),
        'user3': UserFactory(username='user3'),
    }


@pytest.fixture
def user_with_permissions(db):
    """
    Create a user with specific permissions.
    
    Returns:
        User: User with a custom group that has specific permissions
    
    Usage:
        def test_user_permissions(user_with_permissions):
            assert user_with_permissions.groups.exists()
    """
    user = UserFactory(username='user_with_perms')
    group = GroupFactory(name='test_group')
    
    # Add some permissions to the group
    permissions = Permission.objects.filter(
        codename__in=['add_user', 'change_user', 'view_user']
    )
    group.permissions.set(permissions)
    user.groups.add(group)
    
    return user


# ============================================================================
# API Client Fixtures
# ============================================================================

@pytest.fixture
def api_client():
    """
    Create a DRF APIClient instance for testing API endpoints.
    
    Returns:
        APIClient: REST Framework API client
    
    Usage:
        def test_list_blogs(api_client):
            response = api_client.get('/api/blogs/')
            assert response.status_code == 200
    """
    return APIClient()


@pytest.fixture
def authenticated_api_client(api_client, user):
    """
    Create an authenticated APIClient using force_authenticate.
    
    Returns:
        APIClient: Authenticated client ready for protected endpoints
    
    Usage:
        def test_create_blog(authenticated_api_client):
            response = authenticated_api_client.post('/api/blogs/', {...})
            assert response.status_code == 201
    """
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def admin_api_client(api_client, admin_user):
    """
    Create an authenticated APIClient as admin.
    
    Returns:
        APIClient: Admin client for testing admin-only endpoints
    
    Usage:
        def test_admin_action(admin_api_client):
            response = admin_api_client.get('/api/admin/users/')
            assert response.status_code == 200
    """
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def staff_api_client(api_client, staff_user):
    """
    Create an authenticated APIClient as staff.
    
    Returns:
        APIClient: Staff client for testing staff-only endpoints
    
    Usage:
        def test_staff_action(staff_api_client):
            response = staff_api_client.get('/api/staff/dashboard/')
            assert response.status_code == 200
    """
    api_client.force_authenticate(user=staff_user)
    return api_client


@pytest.fixture
def client():
    """
    Create a Django test client for traditional views.
    
    Returns:
        Client: Django's standard test client
    
    Usage:
        def test_form_view(client):
            response = client.post('/contact/', {'name': 'John'})
            assert response.status_code == 302
    """
    return Client()


@pytest.fixture
def authenticated_client(client, user):
    """
    Create an authenticated Django test client.
    
    Returns:
        Client: Authenticated Django client
    
    Usage:
        def test_protected_page(authenticated_client):
            response = authenticated_client.get('/dashboard/')
            assert response.status_code == 200
    """
    client.force_login(user)
    return client


# ============================================================================
# Helper Fixtures
# ============================================================================

@pytest.fixture
def faker():
    """
    Provide Faker instance for generating test data.
    
    Returns:
        Faker: Faker instance for creating random test data
    
    Usage:
        def test_user_generation(faker):
            name = faker.name()
            email = faker.email()
    """
    return fake


@pytest.fixture
def user_factory():
    """
    Provide UserFactory for flexible user creation.
    
    Returns:
        UserFactory: Factory for creating users with custom attributes
    
    Usage:
        def test_user_creation(user_factory):
            user = user_factory.create(username='custom_user')
            assert user.username == 'custom_user'
    """
    return UserFactory


@pytest.fixture
def admin_user_factory():
    """
    Provide AdminUserFactory for admin user creation.
    
    Returns:
        AdminUserFactory: Factory for creating admin users
    """
    return AdminUserFactory


@pytest.fixture
def staff_user_factory():
    """
    Provide StaffUserFactory for staff user creation.
    
    Returns:
        StaffUserFactory: Factory for creating staff users
    """
    return StaffUserFactory


# ============================================================================
# Pytest Configuration Hooks
# ============================================================================

@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """
    Override Django's database setup for tests.
    Can be extended for custom database configuration.
    """
    with django_db_blocker.unblock():
        pass


# ============================================================================
# Custom Markers
# ============================================================================

def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers", "slow: mark test as slow to run"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "unit: mark test as unit test"
    )
    config.addinivalue_line(
        "markers", "auth: mark test as requiring authentication"
    )


# ============================================================================
# Utility Functions
# ============================================================================

def create_test_user(username='testuser', email='test@example.com', password='test123'):
    """
    Utility function to create a test user.
    
    Args:
        username (str): Username for the user
        email (str): Email for the user
        password (str): Password for the user
    
    Returns:
        User: Created user instance
    """
    user = User.objects.create_user(
        username=username,
        email=email,
        password=password
    )
    return user


def create_authenticated_client(user=None):
    """
    Utility function to create an authenticated API client.
    
    Args:
        user (User, optional): User to authenticate. Defaults to new user.
    
    Returns:
        APIClient: Authenticated API client
    """
    if user is None:
        user = create_test_user()
    
    client = APIClient()
    client.force_authenticate(user=user)
    return client
