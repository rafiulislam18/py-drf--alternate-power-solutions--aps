"""
Trash and audit history API (mounted at ``/records/``), staff only.

    GET   trash/                       items in the trash (?type=&q=&page=)
    POST  trash/<type>/<id>/restore/   put an item back
    GET   audit/                       the audit history (?kind=staff|client&actor=&action=&type=&q=&from=&to=&page=)
    GET   history/<type>/<id>/         one item's history (staff and client actions together)

Who sees what (the full rules: docs/trash-and-audit-history.md):
- Trash (``trash.view``): staff see and restore the items they deleted
  themselves, of the types whose ``<section>.view`` they hold. Superusers see
  and restore everything. Nobody deletes anything for good — only the daily
  clean-up does, after ``TRASH_RETENTION_DAYS``.
- Audit history, both pages (staff & admin / clients): ``audit.view_all``.
  An item's own history: the item's ``<section>.view``.
- Audit history is never edited or deleted — there is no endpoint for it.
"""

from datetime import datetime, time, timedelta

from django.apps import apps
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.roles import is_dashboard_admin
from apps.staff.access import denied_message, has_perm

from .models import AuditLog, RestoreBlocked
from .trash import retention_days, trash_types, type_label

PAGE_SIZE = 50

# Names for audited types that aren't the model's own verbose name.
TYPE_LABELS = {
    'auth.user': 'Account',
    'solar_dashboard.site': 'Client site',
    'client_portal.ticket': 'Ticket',
    'solar_estimates.solarestimate': 'Solar estimate',
    'solar_estimates.estimatesettings': 'Estimate settings',
    'solar_estimates.catalogueitem': 'Price list item',
    'whatsapp_import.whatsappmessage': 'WhatsApp message',
    'subscription': 'Subscription',
}


class _Pages(PageNumberPagination):
    page_size = PAGE_SIZE
    page_size_query_param = 'page_size'
    max_page_size = 200


def _forbidden(detail="You don't have permission to do that."):
    return Response({'detail': detail}, status=status.HTTP_403_FORBIDDEN)


def _model_for(object_type):
    try:
        return apps.get_model(object_type)
    except (LookupError, ValueError):
        return None


def fallback_label(object_type):
    """'demo.tool_booking' → 'Tool booking' (a type no longer registered, or demo data)."""
    return object_type.split('.')[-1].replace('_', ' ').capitalize()


def label_for(object_type):
    if object_type in TYPE_LABELS:
        return TYPE_LABELS[object_type]
    model = _model_for(object_type)
    if model is None:
        return fallback_label(object_type)
    return getattr(model, 'trash_label', '') or str(model._meta.verbose_name).capitalize()


def _section_of(model):
    return getattr(model, 'trash_section', '') or getattr(model, 'audit_section', '')


def _can_view_type(user, model):
    return user.is_superuser or has_perm(user, f'{model.trash_section}.view')


def _person(user):
    if user is None:
        return None
    return {'id': user.pk, 'name': user.get_full_name() or user.username}


ACTION_LABELS = dict(AuditLog.ACTIONS)


def action_label(action):
    return ACTION_LABELS.get(action) or action.replace('_', ' ').capitalize()


def _entry(row):
    return {
        'id': row.pk,
        'created_at': row.created_at,
        'actor': {'id': row.actor_id, 'name': row.actor_name} if row.actor_name else None,
        'actor_role': row.actor_role,
        'action': row.action,
        'action_label': action_label(row.action),
        'object_type': row.object_type,
        'type_label': label_for(row.object_type),
        'object_id': row.object_id,
        'object_repr': row.object_repr,
        'changes': [{'field': k, **v} for k, v in (row.changes or {}).items()],
        'summary': row.summary,
    }


class _StaffView(APIView):
    """Signed-in staff only; each method checks its own permission."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not is_dashboard_admin(request.user):
            self.permission_denied(request)


# ── trash ────────────────────────────────────────────────────────────────────

class TrashListView(_StaffView):
    def get(self, request):
        user = request.user
        if not has_perm(user, 'trash.view'):
            return _forbidden(denied_message('trash.view'))
        types = {k: m for k, m in trash_types().items() if _can_view_type(user, m)}
        wanted = request.query_params.get('type', '').strip().lower()
        q = request.query_params.get('q', '').strip().lower()
        keep = retention_days()
        now = timezone.now()

        items = []
        for key, model in types.items():
            if wanted and key != wanted:
                continue
            qs = model.all_objects.trashed().select_related('deleted_by')
            if not user.is_superuser:
                qs = qs.filter(deleted_by=user)
            for obj in qs:
                name = str(obj)
                if q and q not in name.lower():
                    continue
                purge_on = obj.deleted_at + timedelta(days=keep)
                items.append({
                    'type': key,
                    'type_label': type_label(model),
                    'id': obj.pk,
                    'name': name,
                    'deleted_at': obj.deleted_at,
                    'deleted_by': _person(obj.deleted_by),
                    'purge_on': purge_on,
                    'days_left': max(0, (purge_on - now).days),
                })
        items.sort(key=lambda i: i['deleted_at'], reverse=True)

        pages = _Pages()
        page = pages.paginate_queryset(items, request, view=self)
        res = pages.get_paginated_response(page)
        res.data['types'] = sorted(({'key': k, 'label': type_label(m)} for k, m in types.items()),
                                   key=lambda t: t['label'])
        res.data['retention_days'] = keep
        res.data['sees_everyone'] = user.is_superuser
        return res


class TrashRestoreView(_StaffView):
    def post(self, request, type_key, pk):
        user = request.user
        if not has_perm(user, 'trash.view'):
            return _forbidden(denied_message('trash.view'))
        model = trash_types().get(type_key.lower())
        if model is None:
            raise Http404('Unknown item type.')
        if not _can_view_type(user, model):
            return _forbidden(denied_message(f'{model.trash_section}.view'))
        qs = model.all_objects.trashed()
        if not user.is_superuser:
            qs = qs.filter(deleted_by=user)
        obj = qs.filter(pk=pk).first()
        if obj is None:
            raise Http404('That item isn’t in the trash.')
        try:
            obj.restore(user)
        except RestoreBlocked as exc:
            return Response({'detail': f'Couldn’t restore “{obj}”: {exc}'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'restored': True, 'type': type_key.lower(), 'id': obj.pk, 'name': str(obj)})


# ── audit history ────────────────────────────────────────────────────────────

def _local_day(value, end=False):
    try:
        day = datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None
    return timezone.make_aware(datetime.combine(day + timedelta(days=1) if end else day, time.min))


def _by_kind(qs, kind):
    """'client' → client actions; anything else → staff & admin (and the system)."""
    if kind == AuditLog.CLIENT:
        return qs.filter(actor_role=AuditLog.CLIENT)
    return qs.exclude(actor_role=AuditLog.CLIENT)


class AuditLogView(_StaffView):
    def get(self, request):
        if not has_perm(request.user, 'audit.view_all'):
            return _forbidden(denied_message('audit.view_all'))
        p = request.query_params
        kind = AuditLog.CLIENT if p.get('kind') == AuditLog.CLIENT else AuditLog.STAFF
        base = _by_kind(AuditLog.objects.all(), kind)
        qs = base
        if p.get('actor', '').strip():
            actor = p['actor'].strip()
            qs = qs.filter(actor_name='') if actor == 'system' else qs.filter(actor_id=actor if actor.isdigit() else -1)
        if p.get('action', '').strip():
            qs = qs.filter(action=p['action'].strip())
        if p.get('type', '').strip():
            qs = qs.filter(object_type=p['type'].strip().lower())
        if p.get('q', '').strip():
            q = p['q'].strip()
            qs = qs.filter(Q(object_repr__icontains=q) | Q(summary__icontains=q) | Q(actor_name__icontains=q)
                           | Q(object_id=q))
        start = _local_day(p.get('from', '').strip())
        if start:
            qs = qs.filter(created_at__gte=start)
        end = _local_day(p.get('to', '').strip(), end=True)
        if end:
            qs = qs.filter(created_at__lt=end)

        pages = _Pages()
        page = pages.paginate_queryset(qs, request, view=self)
        res = pages.get_paginated_response([_entry(r) for r in page])
        res.data['kind'] = kind
        res.data['filters'] = _audit_filters(base)
        return res


def _audit_filters(qs):
    actors = qs.exclude(actor_name='').order_by('actor_id', '-created_at').values_list('actor_id', 'actor_name')
    people = {}
    for actor_id, name in actors:
        people.setdefault(actor_id, name)  # the newest name they acted under
    used_types = set(qs.order_by().values_list('object_type', flat=True).distinct())
    used_actions = set(qs.order_by().values_list('action', flat=True).distinct())
    return {
        'actors': sorted(({'id': i, 'name': n} for i, n in people.items()), key=lambda a: a['name'].lower()),
        'has_system': qs.filter(actor_name='').exists(),
        'types': sorted(({'key': t, 'label': label_for(t)} for t in used_types), key=lambda t: t['label']),
        'actions': [{'key': a, 'label': action_label(a)}
                    for a in [k for k, _ in AuditLog.ACTIONS] + sorted(used_actions - set(ACTION_LABELS))
                    if a in used_actions],
    }


class ItemHistoryView(_StaffView):
    def get(self, request, type_key, pk):
        model = _model_for(type_key.lower())
        if model is None:
            raise Http404('Unknown item type.')
        section = _section_of(model)
        allowed = (request.user.is_superuser or has_perm(request.user, 'audit.view_all')
                   or (section and has_perm(request.user, f'{section}.view')))
        if not allowed:
            return _forbidden(denied_message(f'{section}.view' if section else 'audit.view_all'))
        rows = AuditLog.objects.filter(object_type=model._meta.label_lower, object_id=str(pk))
        return Response([_entry(r) for r in rows[:500]])
