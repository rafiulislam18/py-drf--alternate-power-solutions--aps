from django.contrib import admin

from apps.records.admin import TrashableAdmin

from .models import Job, JobCategory, JobComment, JobSettings


class JobCommentInline(admin.TabularInline):
    model = JobComment
    extra = 0
    fields = ['created_at', 'author_name', 'body', 'edited_at']
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Job)
class JobAdmin(TrashableAdmin):
    list_display = ['job_id', 'category', 'client_name', 'status', 'payment', 'date_added', 'last_updated']
    list_filter = ['status', 'category', 'payment']
    search_fields = ['client_name', 'task', 'number']
    filter_horizontal = ['assigned_to']
    readonly_fields = ['job_id', 'date_added', 'last_updated', 'created_by', 'unpaid_alert_at', 'stale_alert_at']
    raw_id_fields = ['client', 'site']
    inlines = [JobCommentInline]

    def has_add_permission(self, request):
        return False  # new jobs get their number on the dashboard

    @admin.display(description='Job ID', ordering='number')
    def job_id(self, job):
        return job.job_id


admin.site.register(JobSettings)


@admin.register(JobCategory)
class JobCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'order', 'is_active']
    list_editable = ['order', 'is_active']

    def has_delete_permission(self, request, obj=None):
        return False  # jobs keep pointing at their category; switch it off instead
