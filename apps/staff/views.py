"""
Staff accounts API (mounted at ``/staff/``) — superusers only.

    GET   permissions/        the permission catalogue, by section (+ the default read set)
    GET   accounts/           every staff account (superusers too, read-only)
    POST  accounts/           {username, name, email?, password, confirm_password, permissions?}
    GET   accounts/<id>/
    PATCH accounts/<id>/      {name?, email?, is_active?, permissions?, password + confirm_password?}

Superuser accounts aren't edited here (they always have full access; use the
Django admin). Nobody can deactivate themselves. Turning an account off ends
its sessions straight away (inactive users fail JWT authentication).
"""

import logging
from functools import partial

from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.records import audit
from apps.records.models import AuditLog
from utils.exceptions import custom_exception_handler

from . import catalogue
from .models import StaffAccess
from .permissions import IsSuperuser
from .serializers import StaffAccountSerializer, StaffCreateSerializer, StaffUpdateSerializer

logger = logging.getLogger(__name__)


def staff_users():
    """Staff accounts: role='admin' profiles, plus profile-less Django staff/superusers."""
    return User.objects.filter(
        Q(client_profile__role='admin') | Q(client_profile__isnull=True, is_staff=True)
        | Q(client_profile__isnull=True, is_superuser=True)
    ).select_related('client_profile', 'staff_access').distinct()


def _stored_perms(user):
    """The ticked permission codes, even while the account is switched off."""
    try:
        return catalogue.clean(StaffAccess.objects.get(user=user).permissions)
    except StaffAccess.DoesNotExist:
        return list(catalogue.DEFAULT_PERMS)


def _log_staff_change(by, user, before, password_set):
    after = {**StaffAccountSerializer(user).data, 'permissions': _stored_perms(user)}
    changes = {}
    for field, label in (('name', 'Name'), ('email', 'Email')):
        if before[field] != after[field]:
            changes[field] = audit.change(label, before[field], after[field])
    if before['is_active'] != after['is_active']:
        changes['is_active'] = audit.change('Active', 'Yes' if before['is_active'] else 'No',
                                            'Yes' if after['is_active'] else 'No')
    added = [catalogue.LABELS.get(c, c) for c in after['permissions'] if c not in before['permissions']]
    removed = [catalogue.LABELS.get(c, c) for c in before['permissions'] if c not in after['permissions']]
    if added or removed:
        changes['permissions'] = audit.change('Permissions', '; '.join(removed) and f'Removed: {"; ".join(removed)}',
                                              '; '.join(added) and f'Added: {"; ".join(added)}')
    if changes or password_set:
        audit.log(by, AuditLog.UPDATED, audit.account(user), section='staff', changes=changes,
                  summary='Password reset by a superuser' if password_set else '')


class _SuperuserView(APIView):
    permission_classes = [IsSuperuser]

    def get_exception_handler(self):
        return partial(custom_exception_handler, keep_fields=True)


class PermissionCatalogueView(_SuperuserView):
    def get(self, request):
        return Response({
            'sections': [{'key': s['key'], 'label': s['label'],
                          'perms': [{'code': f"{s['key']}.{a}", 'action': a, 'label': label} for a, label in s['perms']]}
                         for s in catalogue.SECTIONS],
            'default': catalogue.DEFAULT_PERMS,
        })


class StaffAccountListView(_SuperuserView):
    def get(self, request):
        users = staff_users().order_by('-is_superuser', '-is_active', 'first_name', 'username')
        return Response([StaffAccountSerializer(u).data for u in users])

    def post(self, request):
        ser = StaffCreateSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        user = ser.save()
        audit.log(request.user, AuditLog.CREATED, audit.account(user), section='staff',
                  summary=f'New staff account with {len(user.staff_access.permissions)} permission(s)')
        logger.info(f'{request.user.username} created staff account {user.username} '
                    f'with {len(user.staff_access.permissions)} permission(s)')
        return Response(StaffAccountSerializer(user).data, status=status.HTTP_201_CREATED)


class StaffAccountDetailView(_SuperuserView):
    def get_user(self, pk):
        return get_object_or_404(staff_users(), pk=pk)

    def get(self, request, pk):
        return Response(StaffAccountSerializer(self.get_user(pk)).data)

    def patch(self, request, pk):
        user = self.get_user(pk)
        if user.is_superuser:
            return Response({'detail': 'Superusers always have full access — change them in the Django admin.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if user.pk == request.user.pk and request.data.get('is_active') is False:
            return Response({'detail': 'You can’t turn off your own account.'}, status=status.HTTP_400_BAD_REQUEST)
        ser = StaffUpdateSerializer(user, data=request.data, partial=True, context={'request': request})
        ser.is_valid(raise_exception=True)
        before = {**StaffAccountSerializer(user).data, 'permissions': _stored_perms(user)}
        user = ser.save()
        _log_staff_change(request.user, User.objects.get(pk=user.pk), before, 'password' in ser.validated_data)
        logger.info(f'{request.user.username} updated staff account {user.username}: {sorted(ser.validated_data)}')
        return Response(StaffAccountSerializer(User.objects.get(pk=user.pk)).data)
