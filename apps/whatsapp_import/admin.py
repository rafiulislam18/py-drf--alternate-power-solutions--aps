from django.contrib import admin
from django.utils import timezone

from .models import ImportedFile, WhatsAppMessage


@admin.register(ImportedFile)
class ImportedFileAdmin(admin.ModelAdmin):
    list_display = ('name', 'message_count', 'drive_modified_time', 'imported_at')
    search_fields = ('name', 'drive_file_id')
    readonly_fields = ('drive_file_id', 'name', 'drive_modified_time', 'message_count', 'imported_at')
    date_hierarchy = 'imported_at'


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ('sent_at', 'sender', 'short_text', 'chat_name', 'marked_as_job', 'dismissed', 'exported_to_jobs_sheet')
    list_filter = ('marked_as_job', 'dismissed', 'exported_to_jobs_sheet', 'chat_name')
    search_fields = ('sender', 'text', 'chat_name')
    date_hierarchy = 'sent_at'
    readonly_fields = ('sender', 'sent_at', 'text', 'chat_name', 'source_file', 'imported_at',
                       'marked_as_job_at', 'exported_to_jobs_sheet_at', 'dismissed_at')
    # The ops manager toggles the job / dismiss flags; everything else is import data.
    fields = ('sender', 'sent_at', 'text', 'chat_name', 'source_file', 'imported_at',
              'marked_as_job', 'marked_as_job_at', 'dismissed', 'dismissed_at',
              'exported_to_jobs_sheet', 'exported_to_jobs_sheet_at')
    actions = ('mark_as_job', 'unmark_as_job', 'dismiss_messages', 'undismiss_messages')

    @admin.display(description='Message')
    def short_text(self, obj):
        return (obj.text[:60] + '…') if len(obj.text) > 60 else obj.text

    @admin.action(description="Mark selected messages as jobs")
    def mark_as_job(self, request, queryset):
        updated = queryset.filter(marked_as_job=False).update(
            marked_as_job=True, marked_as_job_at=timezone.now()
        )
        self.message_user(request, f"{updated} message(s) marked as jobs.")

    @admin.action(description="Unmark selected messages as jobs")
    def unmark_as_job(self, request, queryset):
        # Only unmark ones not yet pushed to the sheet, to avoid desyncing an
        # already-exported job.
        updated = queryset.filter(exported_to_jobs_sheet=False).update(
            marked_as_job=False, marked_as_job_at=None
        )
        self.message_user(request, f"{updated} message(s) unmarked.")

    @admin.action(description="Dismiss selected as 'not a job'")
    def dismiss_messages(self, request, queryset):
        updated = queryset.filter(dismissed=False).update(
            dismissed=True, dismissed_at=timezone.now()
        )
        self.message_user(request, f"{updated} message(s) dismissed.")

    @admin.action(description="Un-dismiss selected messages")
    def undismiss_messages(self, request, queryset):
        updated = queryset.filter(dismissed=True).update(dismissed=False, dismissed_at=None)
        self.message_user(request, f"{updated} message(s) un-dismissed.")
