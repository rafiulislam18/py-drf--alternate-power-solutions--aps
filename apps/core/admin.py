from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import ClientProfile


class ClientProfileInline(admin.StackedInline):
    model = ClientProfile
    can_delete = False
    verbose_name_plural = 'APS Profile'


class UserAdmin(BaseUserAdmin):
    inlines = [ClientProfileInline]
    list_display = ['username', 'email', 'get_role', 'get_company', 'is_staff']

    @admin.display(description='Role')
    def get_role(self, obj):
        try:
            return obj.client_profile.get_role_display()
        except ClientProfile.DoesNotExist:
            return '—'

    @admin.display(description='Company')
    def get_company(self, obj):
        try:
            return obj.client_profile.company_name
        except ClientProfile.DoesNotExist:
            return '—'


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
