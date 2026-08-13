from django.contrib.auth.models import AbstractBaseUser, Group, Permission, PermissionsMixin
from django.db import models

from .managers import UserManager


class GasGuardUser(AbstractBaseUser, PermissionsMixin):
    """
    Gas Guard's own user, authenticating by email.

    NOTE (merge): Gas Guard is NOT the project's ``AUTH_USER_MODEL`` — the APS
    website keeps Django's default ``auth.User``. So this is a *separate* user
    table (label ``gg_users``) that is not wired into Django's auth/admin login
    or ``createsuperuser``. Gas Guard authenticates its own tokens via
    ``apps.gas_guard.users.authentication.GasGuardJWTAuthentication`` and issues
    them with ``apps.gas_guard.users.tokens.GasGuardRefreshToken``. Because it
    is not the swappable auth model, we build on ``AbstractBaseUser`` +
    ``PermissionsMixin`` (which already provide ``is_superuser``,
    ``is_authenticated``, ``set_password``/``check_password``) and add the
    ``is_staff`` / ``is_active`` flags ourselves.
    """

    class Tier(models.TextChoices):
        DEVICE = 'device', 'Device only'
        SUBSCRIBED = 'subscribed', 'Subscribed'

    email = models.EmailField('email address', unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(auto_now_add=True)

    # Plan tier — gates consumption history, estimated-days and email alerts.
    tier = models.CharField(
        max_length=12, choices=Tier.choices, default=Tier.DEVICE
    )
    # Low-gas email alert preferences (subscriber feature).
    email_alerts_enabled = models.BooleanField(default=False)
    # Alerts go here when set; falls back to the login email otherwise.
    notify_email = models.EmailField(blank=True)

    # Override PermissionsMixin's M2Ms with unique reverse accessors — the
    # default ``user_set`` clashes with the project's ``auth.User`` (E304),
    # since Gas Guard is a *second* user model in the same project.
    groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name='gas_guard_users',
        related_query_name='gas_guard_user',
        verbose_name='groups',
        help_text='The groups this Gas Guard user belongs to.',
    )
    user_permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name='gas_guard_users',
        related_query_name='gas_guard_user',
        verbose_name='user permissions',
        help_text='Specific permissions for this Gas Guard user.',
    )

    USERNAME_FIELD = 'email'
    # Fields prompted for by createsuperuser (besides USERNAME_FIELD + password).
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = 'Gas Guard user'
        verbose_name_plural = 'Gas Guard users'

    def __str__(self):
        return self.email

    def get_full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def get_short_name(self):
        return self.first_name


class PendingRegistration(models.Model):
    """
    A sign-up that hasn't verified its email yet.

    We hold the new account's details here — including the already-hashed
    password — until the emailed 6-digit code is confirmed, at which point a
    real ``GasGuardUser`` is created and the pending row is deleted. This keeps
    unverified sign-ups out of the user table entirely.
    """

    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField()
    # Django password hash (set via user.set_password), never plaintext.
    password = models.CharField(max_length=128)
    verification_code = models.CharField(max_length=6)
    verification_code_expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pending Registration'
        verbose_name_plural = 'Pending Registrations'
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['verification_code']),
        ]

    def __str__(self):
        return f'{self.email} (pending)'

    @property
    def is_expired(self):
        from django.utils import timezone

        return timezone.now() > self.verification_code_expires_at
