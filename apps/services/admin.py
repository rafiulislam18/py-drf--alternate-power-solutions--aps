from django.contrib import admin
from .models import Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "short_description", "appreciation_mark")
    search_fields = ("id", "title", "short_description", "long_description", "features", "appreciation_mark")
    ordering = ("-appreciation_mark", "title")  # Highest marks first
