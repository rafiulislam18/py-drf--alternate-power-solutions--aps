from django.contrib import admin
from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "service", "location", "completion_date", "duration", "appreciation_mark")
    search_fields = ("id", "service__title", "title", "short_description", "long_description", "location", "duration", "features", "appreciation_mark")
    ordering = ("-appreciation_mark", "service__title", "title")  # Highest marks first, then Service titles
