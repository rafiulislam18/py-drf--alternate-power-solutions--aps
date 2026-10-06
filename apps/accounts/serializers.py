import secrets

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.text import slugify
from rest_framework import serializers

from apps.core.roles import get_role

from .login import identifier_taken


def _collapse(value):
    return ' '.join((value or '').split())


def normalise_email(value):
    return (value or '').strip().lower()


def check_password_strength(password, user):
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({'password': list(exc.messages)})


def generate_username(email):
    """A unique, valid username for an account that signs in by email.

    Built from the address's local part (``accounts@x.co.za`` → ``accounts``),
    with a random suffix when taken. It is never the email itself, so an old
    address can't keep working as a login after the email changes.
    """
    base = slugify(email.split('@')[0])[:30].strip('-_') or 'client'
    candidate = base
    while identifier_taken(candidate):
        candidate = f'{base}-{secrets.randbelow(9000) + 1000}'
    return candidate


class _PasswordPairMixin(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def validate(self, data):
        data = super().validate(data)
        if data['password'] != data.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return data


class RegisterSerializer(_PasswordPairMixin):
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField(max_length=254)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)

    def validate_name(self, value):
        value = _collapse(value)
        if not value:
            raise serializers.ValidationError('Enter your name or your company\'s name.')
        return value

    def validate_email(self, value):
        return normalise_email(value)

    def validate_phone(self, value):
        return _collapse(value)

    def validate(self, data):
        data = super().validate(data)
        check_password_strength(data['password'], User(username=data['email'].split('@')[0], email=data['email'],
                                                       first_name=data['name']))
        return data


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value):
        return normalise_email(value)


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=2000)


class ResetPasswordSerializer(_PasswordPairMixin):
    token = serializers.CharField(max_length=2000)


class ChangePasswordSerializer(_PasswordPairMixin):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def validate_current_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Your current password is incorrect.')
        return value

    def validate(self, data):
        data = super().validate(data)
        user = self.context['request'].user
        if data['password'] == data['current_password']:
            raise serializers.ValidationError({'password': 'Choose a password different from your current one.'})
        check_password_strength(data['password'], user)
        return data


class ChangeEmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def validate_current_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Your current password is incorrect.')
        return value

    def validate_email(self, value):
        value = normalise_email(value)
        user = self.context['request'].user
        if identifier_taken(value, exclude_user=user):
            raise serializers.ValidationError('An account with that email already exists.')
        return value


class AccountSerializer(serializers.Serializer):
    """The signed-in account, as the Account settings page shows it."""

    def to_representation(self, user):
        try:
            profile = user.client_profile
        except Exception:  # noqa: BLE001 — staff without a profile
            profile = None
        return {
            'username': user.username,
            'email': user.email,
            'email_verified': bool(profile.email_verified) if profile else bool(user.email),
            'pending_email': profile.pending_email if profile else '',
            'role': get_role(user),
            'company_name': profile.company_name if profile else '',
            'phone': profile.phone if profile else '',
            'image': profile.image.url if profile and profile.image else None,
        }


class AccountUpdateSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=200, required=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)

    def validate_company_name(self, value):
        value = _collapse(value)
        if not value:
            raise serializers.ValidationError('Enter your name or your company\'s name.')
        return value

    def validate_phone(self, value):
        return _collapse(value)
