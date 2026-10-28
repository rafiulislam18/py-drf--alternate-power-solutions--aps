from django.contrib import admin

from apps.records.admin import TrashableAdmin

from .models import Service, Project


class ProjectInline(admin.TabularInline):  # or admin.StackedInline for full form
    model = Project
    extra = 0  # how many empty forms to show for adding new subscriptions
    can_delete = False  # projects go to the trash from their own page
    fields = ('title', 'location', 'completion_date', 'duration', 'appreciation_mark')


@admin.register(Service)
class ServiceAdmin(TrashableAdmin):
    list_display = ("id", "title", "short_description", "appreciation_mark")
    search_fields = ("id", "title", "short_description", "long_description", "features", "appreciation_mark")
    ordering = ("-appreciation_mark", "title")  # Highest marks first
    list_per_page = 10

    inlines = [ProjectInline]  # Show projects under service


@admin.register(Project)
class ProjectAdmin(TrashableAdmin):
    list_display = ("id", "title", "service", "location", "completion_date", "duration", "appreciation_mark")
    search_fields = ("id", "service__title", "title", "short_description", "long_description", "location", "duration", "features", "appreciation_mark")
    ordering = ("-appreciation_mark", "service__title", "title")  # Highest marks first, then Service titles
    list_per_page = 10
