"""
Trash and audit history in the Django admin.

- Audit history — read-only: nothing can be added, changed or deleted.
- ``TrashableAdmin`` — the base for a trashable model's own admin: a
  "Not in trash / In trash / All" filter, and Move to trash / Restore
  actions instead of Django's delete. Nobody can delete anything for good
  here — only the automatic clean-up does (docs/trash-and-audit-history.md).
  Every save is written to the audit history.
"""

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from . import audit
from .models import AuditLog, RestoreBlocked


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: audit history is never added to, changed or deleted by hand."""

    list_display = ['created_at', 'who', 'actor_role', 'action_name', 'object_type', 'object_repr', 'changed_fields']
    list_filter = ['actor_role', 'action', 'object_type', 'section']
    search_fields = ['object_repr', 'actor_name', 'summary', 'object_id']
    date_hierarchy = 'created_at'
    fields = ['created_at', 'who', 'actor_role', 'action_name', 'object_type', 'object_id', 'object_repr', 'summary',
              'changes_table']
    readonly_fields = fields
    list_per_page = 50

    @admin.display(description='Who', ordering='actor_name')
    def who(self, row):
        return row.actor_name or 'System (automatic)'

    @admin.display(description='Action', ordering='action')
    def action_name(self, row):
        return dict(AuditLog.ACTIONS).get(row.action) or row.action.replace('_', ' ').capitalize()

    @admin.display(description='Changed')
    def changed_fields(self, row):
        return ', '.join(c.get('label', k) for k, c in (row.changes or {}).items()) or '—'

    @admin.display(description='Changes')
    def changes_table(self, row):
        if not row.changes:
            return '—'
        rows = format_html_join(
            '', '<tr><td><b>{}</b></td><td>{}</td><td>→</td><td>{}</td></tr>',
            ((c.get('label', k), c.get('old') or '(empty)', c.get('new') or '(empty)') for k, c in row.changes.items()),
        )
        return format_html('<table>{}</table>', rows)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TrashStatusFilter(admin.SimpleListFilter):
    title = 'trash'
    parameter_name = 'trash'

    def lookups(self, request, model_admin):
        return [('live', 'Not in trash'), ('trashed', 'In trash'), ('all', 'All')]

    def choices(self, changelist):
        current = self.value() or 'live'
        for value, label in self.lookup_choices:
            yield {
                'selected': current == value,
                'query_string': changelist.get_query_string({self.parameter_name: value}),
                'display': label,
            }

    def queryset(self, request, queryset):
        value = self.value() or 'live'
        if value == 'trashed':
            return queryset.trashed()
        if value == 'live':
            return queryset.live()
        return queryset


class TrashableAdmin(admin.ModelAdmin):
    """Admin base for a ``TrashableModel`` — see the module docstring."""

    actions = ['move_to_trash', 'restore_from_trash']

    def get_queryset(self, request):
        qs = self.model.all_objects.all()
        ordering = self.get_ordering(request)
        return qs.order_by(*ordering) if ordering else qs

    def get_list_filter(self, request):
        return [TrashStatusFilter, *super().get_list_filter(request)]

    def get_readonly_fields(self, request, obj=None):
        return [*super().get_readonly_fields(request, obj), 'trash_status']

    @admin.display(description='Trash')
    def trash_status(self, obj):
        if not obj or not obj.in_trash:
            return 'Not in trash'
        who = (obj.deleted_by.get_full_name() or obj.deleted_by.username) if obj.deleted_by else 'someone'
        return f'In trash — deleted by {who} on {timezone.localtime(obj.deleted_at):%d %b %Y %H:%M}'

    def has_delete_permission(self, request, obj=None):
        return False  # deleting = Move to trash; nobody deletes for good by hand

    def save_model(self, request, obj, form, change):
        if change:
            before = audit.snapshot(self.model.all_objects.get(pk=obj.pk))
            super().save_model(request, obj, form, change)
            audit.log_update(request.user, obj, before)
        else:
            super().save_model(request, obj, form, change)
            audit.log_create(request.user, obj)

    def save_formset(self, request, form, formset, change):
        # Inline rows (e.g. a service's projects) are audited too.
        before = {obj.pk: audit.snapshot(obj) for obj in formset.get_queryset()}
        for obj in formset.save():
            if obj.pk in before:
                audit.log_update(request.user, obj, before[obj.pk])
            else:
                audit.log_create(request.user, obj)

    @admin.action(description='Move selected to the trash')
    def move_to_trash(self, request, queryset):
        items = list(queryset.live())
        for obj in items:
            obj.trash(request.user)
        self.message_user(request, f'Moved {len(items)} item(s) to the trash.', messages.SUCCESS)

    @admin.action(description='Restore selected from the trash')
    def restore_from_trash(self, request, queryset):
        restored = 0
        for obj in queryset.trashed():
            try:
                obj.restore(request.user)
                restored += 1
            except RestoreBlocked as exc:
                self.message_user(request, f'Couldn’t restore “{obj}”: {exc}', messages.WARNING)
        self.message_user(request, f'Restored {restored} item(s).', messages.SUCCESS)
