from django.contrib import admin

from .models import GasGuardUser, PendingRegistration


@admin.register(GasGuardUser)
class GasGuardUserAdmin(admin.ModelAdmin):
    """Admin for the Gas Guard email-based user.

    NOTE (merge): Gas Guard is not the project ``AUTH_USER_MODEL``, so this is a
    plain ``ModelAdmin`` rather than Django's ``UserAdmin`` — it is a separate
    user table, unrelated to the site's admin/staff login. Passwords are hashed
    and shown read-only; use the app's own registration flow to create users.
    """

    ordering = ['email']
    list_display = ['email', 'first_name', 'last_name', 'tier', 'is_staff', 'is_active']
    list_filter = ['is_staff', 'is_superuser', 'is_active', 'tier']
    search_fields = ['email', 'first_name', 'last_name']
    readonly_fields = ['password', 'last_login', 'date_joined']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name')}),
        ('Gas Guard', {'fields': ('tier', 'email_alerts_enabled', 'notify_email')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    filter_horizontal = ('groups', 'user_permissions')


@admin.register(PendingRegistration)
class PendingRegistrationAdmin(admin.ModelAdmin):
    """Read-only-ish view of sign-ups awaiting email verification."""

    list_display = ['email', 'first_name', 'last_name', 'created_at',
                    'verification_code_expires_at']
    search_fields = ['email', 'first_name', 'last_name']
    readonly_fields = ['password', 'verification_code', 'created_at']
