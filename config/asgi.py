"""
ASGI config: normal HTTP plus the dashboard's WebSocket (/ws/dashboard/), which
pushes live ticket-chat events — see apps/client_portal/consumers.py.

In production gunicorn keeps serving HTTP (WSGI); Daphne serves this for the
/ws/ path only (nginx routes it).
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.urls import path  # noqa: E402

from apps.client_portal.consumers import DashboardConsumer  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AllowedHostsOriginValidator(URLRouter([
        path('ws/dashboard/', DashboardConsumer.as_asgi()),
    ])),
})
