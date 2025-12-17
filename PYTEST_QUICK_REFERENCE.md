# Pytest Quick Reference Card

## Installation Status
✅ All packages installed:
- pytest 9.0.2
- pytest-django 4.11.1  
- pytest-cov 7.0.0
- factory-boy 3.3.3
- faker 38.2.0

## Most Common Commands

### Basic Execution
```bash
pytest                              # Run all tests
pytest -v                          # Verbose output
pytest tests/blog/                 # Run specific app
pytest tests/blog/test_models.py   # Run specific file
pytest -k "blog"                   # Run tests matching pattern
pytest --lf                        # Run last failed tests
pytest -x                          # Stop on first failure
```

### Coverage
```bash
pytest --cov                       # Coverage report
pytest --cov=apps                  # Coverage for specific module
pytest --cov --cov-report=html    # HTML report (open htmlcov/index.html)
```

### Markers (Test Categories)
```bash
pytest -m unit                     # Run unit tests only
pytest -m integration              # Run integration tests only
pytest -m "not slow"               # Skip slow tests
pytest -m auth                     # Run auth-related tests
```

### Debugging
```bash
pytest -s                          # Show print statements
pytest -l                          # Show local variables
pytest --pdb                       # Drop to debugger on failure
pytest -x --pdb                    # Stop on first failure + debug
pytest --tb=short                  # Short traceback format
```

## Using Fixtures in Tests

### Basic Pattern
```python
import pytest
from rest_framework import status

pytestmark = pytest.mark.django_db  # Mark all tests to use DB

class TestMyFeature:
    def test_with_user(self, user):
        """user fixture: authenticated user"""
        assert user.username == 'testuser'
    
    def test_with_admin(self, admin_user):
        """admin_user fixture: superuser"""
        assert admin_user.is_superuser
    
    def test_with_api(self, authenticated_api_client):
        """authenticated_api_client fixture: DRF API client"""
        response = authenticated_api_client.get('/api/endpoint/')
        assert response.status_code == status.HTTP_200_OK
    
    def test_with_data(self, faker):
        """faker fixture: random data generation"""
        name = faker.name()
        email = faker.email()
```

## Available Fixtures

### Database
| Fixture | Purpose |
|---------|---------|
| `db` | Database access |
| `db_setup` | Explicit DB setup |
| `transactional_db` | Transaction support |

### Users
| Fixture | Description |
|---------|-------------|
| `user` | Regular user (username='testuser') |
| `admin_user` | Superuser account |
| `staff_user` | Staff user (not super) |
| `multiple_users` | Dict: {'user1', 'user2', 'user3'} |
| `user_with_permissions` | User with custom group permissions |

### API Clients
| Fixture | Access Level |
|---------|--------------|
| `api_client` | Anonymous/public |
| `authenticated_api_client` | Logged-in user |
| `admin_api_client` | Admin/superuser |
| `staff_api_client` | Staff user |
| `client` | Django test client |
| `authenticated_client` | Django client (logged in) |

### Data Generation
| Fixture | Purpose |
|---------|---------|
| `faker` | Faker instance (random data) |
| `user_factory` | UserFactory (flexible creation) |
| `admin_user_factory` | AdminUserFactory |
| `staff_user_factory` | StaffUserFactory |

## Test Structure Template

```python
"""
Unit/Integration tests for [component].

Tests:
- [What you're testing]
- [Feature 1]
- [Feature 2]
"""

import pytest
from rest_framework import status
from apps.myapp.models import MyModel
from apps.myapp.serializers import MySerializer

pytestmark = pytest.mark.django_db  # All tests need DB


class TestMyModel:
    """Test cases for MyModel."""
    
    @pytest.fixture
    def my_object(self):
        """Create a test object."""
        return MyModel.objects.create(name='Test')
    
    def test_create(self):
        """Test creating object."""
        obj = MyModel.objects.create(name='Test')
        assert obj.name == 'Test'
    
    def test_with_fixture(self, my_object):
        """Test using fixture."""
        assert my_object.name == 'Test'


class TestMyAPI:
    """Test cases for MyModel API."""
    
    def test_public_list(self, api_client):
        """Test unauthenticated access."""
        response = api_client.get('/api/myapp/')
        assert response.status_code == status.HTTP_200_OK
    
    def test_authenticated_create(self, authenticated_api_client):
        """Test authenticated POST."""
        data = {'name': 'New Item'}
        response = authenticated_api_client.post('/api/myapp/', data)
        assert response.status_code == status.HTTP_201_CREATED
    
    def test_admin_delete(self, admin_api_client):
        """Test admin DELETE access."""
        # Create something first
        obj = MyModel.objects.create(name='Delete Me')
        response = admin_api_client.delete(f'/api/myapp/{obj.id}/')
        assert response.status_code == status.HTTP_204_NO_CONTENT
```

## Common Assertions

```python
from rest_framework import status

# Status codes
assert response.status_code == status.HTTP_200_OK
assert response.status_code == status.HTTP_201_CREATED
assert response.status_code == status.HTTP_404_NOT_FOUND
assert response.status_code == status.HTTP_403_FORBIDDEN

# Data assertions
assert response.data['name'] == 'Expected Value'
assert len(response.data) == 5
assert 'key' in response.data

# Database assertions
assert MyModel.objects.count() == 1
assert obj in MyModel.objects.all()
assert MyModel.objects.filter(name='Test').exists()

# User/Auth assertions
assert user.is_staff is True
assert user.is_superuser is False
assert user.has_perm('app.change_model')

# Exception testing
import pytest
with pytest.raises(ValueError):
    bad_function()
```

## File Organization Pattern

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── TESTING.md               # Full documentation
│
├── app_name/
│   ├── __init__.py
│   ├── test_models.py       # Unit tests: model logic
│   ├── test_serializers.py  # Unit tests: serializer logic
│   ├── test_views.py        # Integration tests: API endpoints
│   └── unit/
│       └── test_services.py # Unit tests: business logic
│
├── another_app/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_views.py
│   └── ...
```

## Configuration Files

### pytest.ini
- Settings module: `config.test_settings`
- Test path: `tests/`
- Auto-coverage on every run
- Custom markers for organization

### config/settings/test.py
- Database: In-memory SQLite (instant)
- Migrations: Disabled (10x faster)
- Logging: Suppressed
- Security: Disabled (faster)
- Emails: Console backend

## Helpful Commands

```bash
# See all available fixtures
pytest --fixtures

# See all test markers
pytest --markers

# Show test collection without running
pytest --collect-only

# Run tests in parallel (requires pytest-xdist)
pytest -n auto

# Run with minimal output
pytest -q

# Run with maximum verbosity
pytest -vv

# Create JUnit XML report (for CI/CD)
pytest --junit-xml=report.xml

# Re-run failed tests from last run
pytest --lf

# Run tests until first failure
pytest -x
```

## Tips & Tricks

### 1. Use TDD (Test-Driven Development)
```bash
# Write test first, then code
pytest -x --lf  # Run last failed test only
```

### 2. Test Organization
- Group related tests in classes
- Use descriptive test names
- One assertion per test (usually)

### 3. Skip Slow Tests During Development
```python
@pytest.mark.slow
def test_heavy_operation():
    pass

# Run without slow tests:
# pytest -m "not slow"
```

### 4. Custom Fixtures for Specific Tests
```python
@pytest.fixture
def blog_with_posts(db):
    """Create blog with 5 posts."""
    category = BlogCategory.objects.create(name='Tech')
    for i in range(5):
        Blog.objects.create(category=category, ...)
    return category

def test_blog_queries(blog_with_posts):
    assert blog_with_posts.blogs.count() == 5
```

### 5. Mock External Services
```python
from unittest.mock import patch

def test_api_call(api_client):
    with patch('apps.chatbot.views.chatbot.get_response') as mock:
        mock.return_value = {'success': True}
        response = api_client.post('/chatbot/', {'message': 'Hi'})
        assert response.status_code == 200
```

## Troubleshooting Quick Fixes

| Problem | Solution |
|---------|----------|
| "django.db.utils.ProgrammingError" | Make sure `MIGRATION_MODULES = DisableMigrations()` in test_settings.py |
| "fixture 'user' not found" | Ensure conftest.py is in tests/ directory |
| Tests fail together but pass individually | Check for database state pollution; use fixtures properly |
| Tests are slow | Check if migrations are being run; use in-memory DB |

## Resources

- **Full Guide**: [tests/TESTING.md](tests/TESTING.md)
- **Pytest Docs**: https://docs.pytest.org/
- **pytest-django**: https://pytest-django.readthedocs.io/
- **Django Testing**: https://docs.djangoproject.com/en/5.2/topics/testing/
- **DRF Testing**: https://www.django-rest-framework.org/api-guide/testing/

---

**Last Updated**: December 2025  
**Version**: 1.0  
**Project**: Alternate Power Solutions (APS)
