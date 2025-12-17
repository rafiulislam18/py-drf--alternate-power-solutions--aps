# Django REST Framework Testing Guide

Comprehensive pytest-based testing setup for the Alternate Power Solutions Django REST Framework project.

## Table of Contents

- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Running Tests](#running-tests)
- [Writing Tests](#writing-tests)
- [Fixtures and Utilities](#fixtures-and-utilities)
- [Best Practices](#best-practices)
- [CI/CD Integration](#cicd-integration)
- [Troubleshooting](#troubleshooting)

## Quick Start

### Installation

All testing dependencies are already installed. If needed, install them manually:

```bash
pip install pytest pytest-django pytest-cov factory-boy faker
```

### Running Your First Test

```bash
# Run all tests
pytest

# Run tests with coverage report
pytest --cov

# Run tests for specific app
pytest tests/blog/

# Run specific test file
pytest tests/blog/test_models.py

# Run specific test class
pytest tests/blog/test_models.py::TestBlogCategory

# Run specific test
pytest tests/blog/test_models.py::TestBlogCategory::test_create_blog_category
```

## Project Structure

```
project-root/
├── pytest.ini                          # Pytest configuration
├── config/
│   ├── settings.py
│   ├── test_settings.py                # Test settings module (optimized for speed)
│   ├── urls.py
│   ├── wsgi.py
├── apps/                               # Django applications
│   ├── blog/
│   ├── chatbot/
│   ├── quote_request/
│   └── ...
├── tests/                              # Top-level test directory
│   ├── __init__.py
│   ├── conftest.py                     # Shared fixtures and configuration
│   ├── blog/                           # Blog app tests
│   │   ├── __init__.py
│   │   ├── test_models.py              # Unit tests for models
│   │   ├── test_serializers.py         # Unit tests for serializers
│   │   └── test_views.py               # Integration tests for views/API
│   ├── chatbot/                        # Chatbot app tests
│   │   ├── __init__.py
│   │   └── test_views.py
│   ├── quote_request/
│   └── ...                             # Add for each app
├── test_requirements.txt
└── manage.py
```

### Test File Organization

For each Django app, create the following structure under `tests/{app_name}/`:

```
tests/{app_name}/
├── __init__.py
├── test_models.py           # Unit tests for model logic
├── test_serializers.py      # Unit tests for serializer logic
├── test_views.py            # Integration tests for API endpoints
└── unit/                    # (Optional) Subdirectory for unit tests
    └── test_services.py     # Tests for business logic
```

**Test Organization by Responsibility:**

- **test_models.py**: Model creation, validation, relationships, methods
- **test_serializers.py**: Serializer validation, deserialization, field constraints
- **test_views.py**: API endpoints, permissions, response structure, status codes
- **unit/test_services.py**: Business logic, utility functions, helper methods

## Configuration

### pytest.ini

Located in the project root, contains:

- **DJANGO_SETTINGS_MODULE**: Points to `config.test_settings`
- **testpaths**: Specifies `tests/` directory
- **addopts**: Coverage reporting, verbose output, slowest tests
- **markers**: Custom test categories

### config/test_settings.py

Optimized settings for fast test execution:

```python
# In-memory database for speed
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Disabled migrations for 10x faster tests
MIGRATION_MODULES = DisableMigrations()

# Console email backend (no actual emails sent)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Synchronous Celery tasks
CELERY_TASK_ALWAYS_EAGER = True

# Disabled security features
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
```

## Running Tests

### Basic Commands

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run tests matching pattern
pytest -k "test_create"

# Run specific test file
pytest tests/blog/test_models.py

# Run specific test class
pytest tests/blog/test_models.py::TestBlogCategory

# Run specific test method
pytest tests/blog/test_models.py::TestBlogCategory::test_create_blog_category

# Stop on first failure
pytest -x

# Show local variables in tracebacks
pytest -l

# Show slowest 10 tests
pytest --durations=10
```

### Coverage Reports

```bash
# Generate coverage report (terminal + HTML)
pytest --cov

# Coverage for specific modules
pytest --cov=apps --cov=config

# Generate only HTML report
pytest --cov --cov-report=html

# Open HTML coverage report
# On Windows:
start htmlcov/index.html
# On macOS:
open htmlcov/index.html
# On Linux:
xdg-open htmlcov/index.html
```

### Test Selection with Markers

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run tests that require authentication
pytest -m auth

# Run tests except slow ones
pytest -m "not slow"

# Run specific type of tests
pytest -m models
pytest -m serializers
pytest -m views
```

### Parallel Execution (Optional)

Install pytest-xdist for parallel test execution:

```bash
pip install pytest-xdist
```

Then run:

```bash
# Run tests in parallel (auto-detect CPU cores)
pytest -n auto

# Run with specific number of workers
pytest -n 4
```

## Writing Tests

### Test File Template

```python
"""
Unit tests for [component].

Tests:
- [What you're testing]
- [Feature 1]
- [Feature 2]
"""

import pytest
from rest_framework import status
from apps.blog.models import Blog, BlogCategory
from apps.blog.serializers import BlogSerializer


# Mark all tests in this file to use database
pytestmark = pytest.mark.django_db


class TestBlogModel:
    """Test cases for Blog model."""
    
    @pytest.fixture
    def blog_category(self):
        """Create a test blog category."""
        return BlogCategory.objects.create(name='Tech')
    
    def test_create_blog(self, blog_category):
        """Test creating a blog post."""
        blog = Blog.objects.create(
            category=blog_category,
            title='Django Guide',
            # ... other fields
        )
        assert blog.title == 'Django Guide'
    
    def test_blog_string_representation(self, blog_category):
        """Test __str__ method."""
        blog = Blog.objects.create(
            category=blog_category,
            title='Test',
            # ... other fields
        )
        assert str(blog) == 'Test'


class TestBlogAPI:
    """Test cases for Blog API endpoints."""
    
    def test_list_blogs_unauthorized(self, api_client):
        """Test unauthenticated access to blog list."""
        response = api_client.get('/blogs/')
        assert response.status_code == status.HTTP_200_OK
    
    def test_create_blog_authenticated(self, authenticated_api_client):
        """Test creating blog as authenticated user."""
        data = {
            'category': 1,
            'title': 'New Blog',
            # ... other fields
        }
        response = authenticated_api_client.post('/blogs/', data)
        assert response.status_code in [status.HTTP_201_CREATED, status.HTTP_403_FORBIDDEN]
```

### Using Fixtures

#### Database Fixtures

```python
def test_with_database(db):
    """Test function has database access."""
    User.objects.create(username='test')
    assert User.objects.count() == 1


def test_with_transactional_db(transactional_db):
    """Test with transaction support (slower but handles edge cases)."""
    User.objects.create(username='test')
```

#### User Fixtures

```python
def test_with_user(user):
    """Test with a regular authenticated user."""
    assert user.username == 'testuser'
    assert user.is_staff is False


def test_with_admin(admin_user):
    """Test with admin/superuser."""
    assert admin_user.is_superuser is True


def test_with_staff(staff_user):
    """Test with staff user."""
    assert staff_user.is_staff is True
    assert staff_user.is_superuser is False


def test_with_multiple_users(multiple_users):
    """Test with multiple users."""
    user1 = multiple_users['user1']
    user2 = multiple_users['user2']
```

#### API Client Fixtures

```python
def test_public_endpoint(api_client):
    """Test with unauthenticated API client."""
    response = api_client.get('/api/public/')
    assert response.status_code == status.HTTP_200_OK


def test_protected_endpoint(authenticated_api_client):
    """Test with authenticated API client."""
    response = authenticated_api_client.get('/api/protected/')
    assert response.status_code == status.HTTP_200_OK


def test_admin_endpoint(admin_api_client):
    """Test with admin API client."""
    response = admin_api_client.get('/api/admin/')
    assert response.status_code == status.HTTP_200_OK


def test_django_client(authenticated_client):
    """Test with authenticated Django client (for traditional views)."""
    response = authenticated_client.get('/dashboard/')
    assert response.status_code == status.HTTP_200_OK
```

#### Data Generation Fixtures

```python
def test_with_faker(faker):
    """Use Faker for random data generation."""
    name = faker.name()
    email = faker.email()
    phone = faker.phone_number()


def test_with_factory(user_factory):
    """Use Factory Boy for flexible object creation."""
    user = user_factory.create(
        username='custom_user',
        email='custom@example.com'
    )
    assert user.username == 'custom_user'
```

## Fixtures and Utilities

### Available Fixtures in conftest.py

#### Database Fixtures
- `db` - Basic database access
- `db_setup` - Explicit database setup
- `transactional_db` - Transaction support

#### User Fixtures
- `user` - Regular authenticated user
- `admin_user` - Superuser account
- `staff_user` - Staff user (not super)
- `multiple_users` - Dict with user1, user2, user3
- `user_with_permissions` - User with custom group permissions

#### API Client Fixtures
- `api_client` - Unauthenticated REST API client
- `authenticated_api_client` - Authenticated API client
- `admin_api_client` - Admin authenticated API client
- `staff_api_client` - Staff authenticated API client
- `client` - Django test client (traditional views)
- `authenticated_client` - Authenticated Django client

#### Data Generation Fixtures
- `faker` - Faker instance for random data
- `user_factory` - UserFactory for custom user creation
- `admin_user_factory` - AdminUserFactory
- `staff_user_factory` - StaffUserFactory

### Creating Custom Fixtures

```python
# In tests/conftest.py

@pytest.fixture
def blog_with_posts(db):
    """Custom fixture - blog category with posts."""
    category = BlogCategory.objects.create(name='Tech')
    for i in range(5):
        Blog.objects.create(
            category=category,
            title=f'Blog {i}',
            # ... other fields
        )
    return category


# In test file
def test_something(blog_with_posts):
    assert blog_with_posts.blogs.count() == 5
```

### Utility Functions

In `conftest.py`:

```python
def create_test_user(username='testuser', email='test@example.com', password='test123'):
    """Create a test user."""
    return User.objects.create_user(
        username=username,
        email=email,
        password=password
    )

def create_authenticated_client(user=None):
    """Create authenticated API client."""
    if user is None:
        user = create_test_user()
    client = APIClient()
    client.force_authenticate(user=user)
    return client
```

## Best Practices

### 1. Test Organization

```python
# ✅ Good: Descriptive test names
def test_user_can_create_blog_post_with_valid_data(self):
    pass

# ❌ Bad: Vague test names
def test_create(self):
    pass
```

### 2. Test Structure (AAA Pattern)

```python
def test_blog_creation(self, blog_category):
    """Test creating a blog - arrange, act, assert."""
    # Arrange
    blog_data = {
        'category': blog_category,
        'title': 'Test Blog',
        # ...
    }
    
    # Act
    blog = Blog.objects.create(**blog_data)
    
    # Assert
    assert blog.title == 'Test Blog'
    assert blog.category == blog_category
```

### 3. Use Meaningful Assertions

```python
# ✅ Good: Specific assertions
assert user.is_staff is True
assert response.status_code == status.HTTP_201_CREATED

# ❌ Bad: Vague assertions
assert user
assert response
```

### 4. Test Isolation

```python
# ✅ Good: Each test is independent
def test_category_1(self):
    BlogCategory.objects.create(name='Tech')
    # ...

def test_category_2(self):
    BlogCategory.objects.create(name='Travel')
    # Independent of test_category_1

# ❌ Bad: Tests depend on execution order
def test_create_first(self):
    self.category = BlogCategory.objects.create(name='Tech')

def test_use_created(self):
    # Fails if test_create_first doesn't run first
    assert self.category.name == 'Tech'
```

### 5. Mock External Services

```python
from unittest.mock import patch, MagicMock

def test_chatbot_response(self, api_client):
    """Mock external chatbot API."""
    with patch('apps.chatbot.views.chatbot.get_response') as mock:
        mock.return_value = {
            'success': True,
            'response': 'Hello'
        }
        response = api_client.post('/chatbot/chat-completions/', 
                                  {'message': 'Hi'})
        assert response.status_code == status.HTTP_200_OK
```

### 6. Test Database-Heavy Operations

```python
# ✅ Good: Use efficient queries in tests
def test_bulk_user_creation(self):
    users = [User(username=f'user_{i}') for i in range(100)]
    User.objects.bulk_create(users)
    assert User.objects.count() == 100

# ❌ Bad: Inefficient queries (N+1 problem)
def test_bulk_user_creation(self):
    for i in range(100):
        User.objects.create(username=f'user_{i}')  # 100 queries!
```

### 7. Error Testing

```python
import pytest

def test_invalid_user_data(self):
    """Test that invalid data raises error."""
    with pytest.raises(IntegrityError):
        User.objects.create(username=None)


def test_api_error_response(self, api_client):
    """Test API returns proper error status."""
    response = api_client.post('/api/blogs/', {})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'error' in response.data
```

### 8. Document Test Purpose

```python
def test_admin_can_delete_any_blog(self, admin_api_client, blog):
    """
    Test that admin users can delete any blog.
    
    Related to permission requirement:
    Only admins should be able to delete blogs.
    """
    response = admin_api_client.delete(f'/blogs/{blog.id}/')
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Blog.objects.filter(id=blog.id).exists()
```

## CI/CD Integration

### GitHub Actions Example

Create `.github/workflows/tests.yml`:

```yaml
name: Django Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v2
    
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r test_requirements.txt
    
    - name: Run tests with coverage
      run: |
        pytest --cov --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v2
```

### Jenkins Example

Create `Jenkinsfile`:

```groovy
pipeline {
    agent any
    
    stages {
        stage('Test') {
            steps {
                sh 'pip install -r test_requirements.txt'
                sh 'pytest --cov --cov-report=xml --junit-xml=test-results.xml'
            }
        }
    }
    
    post {
        always {
            junit 'test-results.xml'
            publishCoverage adapters: [coberturaAdapter('coverage.xml')]
        }
    }
}
```

### GitLab CI Example

Create `.gitlab-ci.yml`:

```yaml
test:
  image: python:3.11
  script:
    - pip install -r test_requirements.txt
    - pytest --cov --cov-report=term --cov-report=html
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    paths:
      - htmlcov/
```

## Troubleshooting

### Common Issues

**1. Tests fail with "django.db.utils.ProgrammingError: relation does not exist"**

```bash
# Solution: Make sure migrations are disabled in test settings
# Check config/test_settings.py has:
MIGRATION_MODULES = DisableMigrations()
```

**2. Fixtures not working / "fixture 'user' not found"**

```bash
# Solution: Ensure conftest.py is in tests/ directory and properly formatted
# Run: pytest --fixtures  # to see available fixtures
```

**3. Tests pass individually but fail when run together**

```bash
# Solution: Likely database state issue. Ensure:
# - Tests use proper fixtures (db, transactional_db)
# - No shared state between tests
# - Use @pytest.fixture with scope='function' (default)
```

**4. Tests are slow**

```bash
# Solution: Check what's slow
pytest --durations=10

# Optimize:
# - Use in-memory database (already in test settings)
# - Disable migrations (already disabled in test settings)
# - Use fewer database queries
# - Consider @pytest.mark.slow for long-running tests
```

**5. Import errors in tests**

```bash
# Solution: Ensure PYTHONPATH is correct
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

**6. "No module named 'django'" in tests**

```bash
# Solution: Activate virtual environment
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate  # Windows
```

### Debug Mode

Run tests with debugging:

```bash
# Stop on first failure with debugger
pytest -x --pdb

# Drop to debugger on exceptions
pytest --pdb

# Show print statements
pytest -s

# Show local variables in tracebacks
pytest -l
```

### Getting Help

```bash
# Show pytest help
pytest --help

# Show available fixtures
pytest --fixtures

# Show test markers
pytest --markers

# Show verbose output
pytest -v --tb=long
```

## Advanced Topics

### Parametrized Tests

```python
@pytest.mark.parametrize('username,email', [
    ('user1', 'user1@example.com'),
    ('user2', 'user2@example.com'),
    ('user3', 'user3@example.com'),
])
def test_multiple_users(username, email):
    """Test with multiple input sets."""
    user = User.objects.create_user(username=username, email=email)
    assert user.username == username
```

### Custom Markers

```python
@pytest.mark.slow
def test_heavy_computation():
    """Run with: pytest -m slow"""
    pass

@pytest.mark.integration
def test_api_integration():
    """Run with: pytest -m integration"""
    pass
```

### Test Skipping

```python
@pytest.mark.skip(reason='Not implemented yet')
def test_future_feature():
    pass

@pytest.mark.skipif(not settings.DEBUG, reason='Only in debug mode')
def test_debug_only():
    pass
```

## Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [pytest-django Documentation](https://pytest-django.readthedocs.io/)
- [Django Testing Documentation](https://docs.djangoproject.com/en/5.2/topics/testing/)
- [Django REST Framework Testing](https://www.django-rest-framework.org/api-guide/testing/)
- [Factory Boy Documentation](https://factoryboy.readthedocs.io/)
- [Faker Documentation](https://faker.readthedocs.io/)

## Contributing

When adding new features:

1. **Write tests first** (TDD approach)
2. **Follow the existing test structure** (model, serializer, view tests)
3. **Aim for >80% code coverage**
4. **Test edge cases and error scenarios**
5. **Update this README** if adding new testing patterns

---

**Last Updated**: December 2025  
**Python Version**: 3.10+  
**Django Version**: 5.2.6  
**DRF Version**: 3.16.1
