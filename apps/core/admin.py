from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import ClientProfile
from .roles import get_role


class ClientProfileInline(admin.StackedInline):
    model = ClientProfile
    can_delete = False
    verbose_name_plural = 'APS Profile'


class UserAdmin(BaseUserAdmin):
    inlines = [ClientProfileInline]
    list_display = ['username', 'email', 'email_verified', 'get_role', 'get_company', 'is_staff']

    @admin.display(description='Dashboard role')
    def get_role(self, obj):
        return get_role(obj).capitalize()

    @admin.display(description='Email verified', boolean=True)
    def email_verified(self, obj):
        try:
            return obj.client_profile.email_verified
        except ClientProfile.DoesNotExist:
            return None

    @admin.display(description='Company')
    def get_company(self, obj):
        try:
            return obj.client_profile.company_name
        except ClientProfile.DoesNotExist:
            return '—'


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
