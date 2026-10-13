from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.core.models import ClientProfile

from . import catalogue
from .access import permissions_for
from .models import StaffAccess


def _name(user):
    return user.get_full_name() or (getattr(getattr(user, 'client_profile', None), 'company_name', '') or '')


class StaffAccountSerializer(serializers.Serializer):
    """A staff account as the Staff accounts page shows it."""

    def to_representation(self, user):
        return {
            'id': user.pk,
            'username': user.username,
            'name': _name(user),
            'email': user.email,
            'is_active': user.is_active,
            'is_superuser': user.is_superuser,
            'permissions': permissions_for(user) if user.is_active else [],
            'last_login': user.last_login,
            'date_joined': user.date_joined,
        }


class _PermissionsField(serializers.ListField):
    def __init__(self, **kw):
        super().__init__(child=serializers.CharField(max_length=60), **kw)

    def to_internal_value(self, data):
        codes = super().to_internal_value(data)
        unknown = sorted(set(codes) - set(catalogue.ALL_PERMS))
        if unknown:
            raise serializers.ValidationError(f'Unknown permission(s): {", ".join(unknown)}.')
        return catalogue.clean(codes)


def _check_password(password, username='', email=''):
    try:
        validate_password(password, user=User(username=username, email=email))
    except DjangoValidationError as exc:
        raise serializers.ValidationError({'password': list(exc.messages)})


def _check_email(value, exclude=None):
    value = User.objects.normalize_email((value or '').strip())
    if not value:
        return ''
    others = User.objects.exclude(pk=exclude.pk) if exclude else User.objects.all()
    if others.filter(email__iexact=value).exists():
        raise serializers.ValidationError('An account with that email already exists.')
    if others.filter(username__iexact=value).exists():
        raise serializers.ValidationError('That email is already used as another account’s username.')
    return value


class StaffCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField(max_length=254, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, max_length=128)
    confirm_password = serializers.CharField(write_only=True, max_length=128)
    # Read permissions are on unless the superuser unticked them.
    permissions = _PermissionsField(required=False, default=lambda: list(catalogue.DEFAULT_PERMS))

    def validate_username(self, value):
        value = value.strip()
        try:
            User._meta.get_field('username').run_validators(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('A user with that username already exists.')
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('That username is already used as another account’s email.')
        return value

    def validate_name(self, value):
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('Enter the person’s name.')
        return value

    def validate_email(self, value):
        return _check_email(value)

    def validate(self, data):
        if data['password'] != data.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        _check_password(data['password'], data.get('username', ''), data.get('email', ''))
        return data

    @transaction.atomic
    def create(self, data):
        first, _, last = data['name'].partition(' ')
        user = User(username=data['username'], email=data.get('email', ''), first_name=first[:150],
                    last_name=last[:150], is_staff=False, is_superuser=False)
        user.set_password(data['password'])
        user.save()
        # role='admin' makes it a staff account; APS set the email, so it counts as confirmed.
        ClientProfile.objects.create(user=user, role='admin', company_name=data['name'][:200],
                                     verified_email=user.email)
        StaffAccess.objects.create(user=user, permissions=data['permissions'],
                                   created_by=self.context['request'].user)
        return user


class StaffUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(max_length=254, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    permissions = _PermissionsField(required=False)
    password = serializers.CharField(write_only=True, max_length=128, required=False)
    confirm_password = serializers.CharField(write_only=True, max_length=128, required=False)

    def validate_name(self, value):
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('Enter the person’s name.')
        return value

    def validate_email(self, value):
        return _check_email(value, exclude=self.instance)

    def validate(self, data):
        if 'password' in data or 'confirm_password' in data:
            if data.get('password') != data.pop('confirm_password', None):
                raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
            _check_password(data['password'], self.instance.username, self.instance.email)
        return data

    @transaction.atomic
    def update(self, user, data):
        if 'name' in data:
            user.first_name, _, user.last_name = data['name'].partition(' ')
            user.first_name, user.last_name = user.first_name[:150], user.last_name[:150]
        if 'email' in data:
            user.email = data['email']
        if 'is_active' in data:
            user.is_active = data['is_active']
        if 'password' in data:
            user.set_password(data['password'])  # also ends their other sessions (password version)
        user.save()
        profile, _ = ClientProfile.objects.get_or_create(user=user, defaults={'role': 'admin'})
        if 'name' in data:
            profile.company_name = data['name'][:200]
        if 'email' in data:
            profile.verified_email = user.email
        profile.save()
        if 'permissions' in data:
            access, _ = StaffAccess.objects.get_or_create(user=user, defaults={'created_by': self.context['request'].user})
            access.permissions = data['permissions']
            access.save()
        user._dashboard_perms = None
        return user
