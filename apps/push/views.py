"""
Push notification endpoints. The same views serve both apps, each with its own
users and sign-in (see urls.py):

- APS dashboard (staff and clients): ``/push/aps/…``, dashboard JWT;
- Gas Guard: ``/gas-guard/push/…``, Gas Guard JWT.

``GET  config/``                public: the VAPID public key and whether push is set up
``GET  subscriptions/``         my devices
``POST subscriptions/``         turn notifications on for this device ({endpoint, keys})
``DELETE subscriptions/<id>/``  remove one of my devices
``POST unsubscribe/``           turn this device off ({endpoint}); also on sign-out
``POST test/``                  send a test notification now ({endpoint} = this device only)

The test is sent straight away, not through Celery, so the button can say
whether it worked.
"""

import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.records import audit

from .devices import device_label
from .models import PushSubscription
from .send import deliver, is_configured
from .serializers import DeviceSerializer, EndpointSerializer, OptionalEndpointSerializer, SubscribeSerializer
from .throttling import PushTestThrottle, PushWriteThrottle

logger = logging.getLogger('apps.push')

# Most devices one account keeps; the oldest go when a new one is added.
MAX_DEVICES = 20
NOT_SET_UP = "Notifications aren't set up on the server yet."
TEST_URL = {'aps': '/dashboard/account', 'gas_guard': '/gas-guard/dashboard/settings'}


class PushConfigView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'enabled': is_configured(), 'public_key': settings.VAPID_PUBLIC_KEY or ''})


class _PushView(APIView):
    app = ''  # PushSubscription.App value, set in urls.py
    permission_classes = [IsAuthenticated]
    throttle_classes = [PushWriteThrottle]

    def owner(self):
        return {'user': self.request.user} if self.app == PushSubscription.App.APS else {'gg_user': self.request.user}

    def mine(self):
        return PushSubscription.objects.filter(app=self.app, **self.owner())

    def log(self, action, sub):
        """APS only: the audit history is for dashboard accounts (Gas Guard users aren't in it)."""
        if self.app == PushSubscription.App.APS:
            audit.log(self.request.user, action, audit.account(self.request.user), section='',
                      summary=sub.device_label)
        logger.info(f'{self.app} push {action}: {self.request.user} · {sub.device_label}')


class SubscriptionListView(_PushView):
    def get(self, request):
        return Response(DeviceSerializer(self.mine(), many=True).data)

    def post(self, request):
        if not is_configured():
            return Response({'detail': NOT_SET_UP}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = SubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        fields = {
            'app': self.app, 'user': None, 'gg_user': None, **self.owner(),
            'p256dh': data['keys']['p256dh'], 'auth': data['keys']['auth'],
            'device_label': device_label(request.META.get('HTTP_USER_AGENT')),
        }
        is_new = not self.mine().filter(endpoint=data['endpoint']).exists()
        # The same browser again (keys refreshed) updates its row; a shared
        # device now signed in as someone else becomes theirs.
        sub, _ = PushSubscription.objects.update_or_create(endpoint=data['endpoint'], defaults=fields)
        stale = self.mine().order_by('-created_at').values_list('pk', flat=True)[MAX_DEVICES:]
        PushSubscription.objects.filter(pk__in=list(stale)).delete()
        if is_new:
            self.log('notifications_on', sub)
        return Response(DeviceSerializer(sub).data, status=status.HTTP_201_CREATED if is_new else status.HTTP_200_OK)


class SubscriptionDetailView(_PushView):
    def delete(self, request, pk):
        sub = get_object_or_404(self.mine(), pk=pk)
        sub.delete()
        self.log('notifications_off', sub)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UnsubscribeView(_PushView):
    def post(self, request):
        serializer = EndpointSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sub = self.mine().filter(endpoint=serializer.validated_data['endpoint']).first()
        if sub is not None:
            sub.delete()
            self.log('notifications_off', sub)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestPushView(_PushView):
    throttle_classes = [PushTestThrottle]

    def post(self, request):
        if not is_configured():
            return Response({'detail': NOT_SET_UP}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = OptionalEndpointSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subs = self.mine()
        endpoint = serializer.validated_data.get('endpoint')
        if endpoint:
            subs = subs.filter(endpoint=endpoint)
        subs = list(subs)
        if not subs:
            detail = "This device isn't set up for notifications." if endpoint else 'Turn notifications on first.'
            return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)
        payload = {
            'app': self.app,
            'title': 'Test notification',
            'body': 'Notifications are working on this device.',
            'tag': f'{self.app}-test',
            'url': TEST_URL[self.app],
        }
        counts = deliver(subs, payload)
        if counts['sent']:
            detail = 'Test notification sent.'
        elif counts['gone']:
            detail = "This device's notification sign-up has expired. Turn notifications on again."
        else:
            detail = "The test notification couldn't be sent. Try again in a minute."
        return Response({'detail': detail, **counts})
