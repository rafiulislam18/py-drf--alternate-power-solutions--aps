"""
Push endpoints for both apps (see views.py). ``urlpatterns`` is the APS
dashboard's set, mounted at ``/push/aps/`` by config/urls.py; Gas Guard's set
(``gas_guard_urlpatterns``) is mounted at ``/gas-guard/push/`` by
apps/gas_guard/urls.py, with Gas Guard's own sign-in.
"""

from django.urls import path

from apps.gas_guard.users.authentication import GasGuardJWTAuthentication

from . import views


def _patterns(app, prefix, **auth):
    kw = {'app': app, **auth}
    return [
        path('config/', views.PushConfigView.as_view(), name=f'{prefix}-push-config'),
        path('subscriptions/', views.SubscriptionListView.as_view(**kw), name=f'{prefix}-push-subscriptions'),
        path('subscriptions/<int:pk>/', views.SubscriptionDetailView.as_view(**kw),
             name=f'{prefix}-push-subscription'),
        path('unsubscribe/', views.UnsubscribeView.as_view(**kw), name=f'{prefix}-push-unsubscribe'),
        path('test/', views.TestPushView.as_view(**kw), name=f'{prefix}-push-test'),
    ]


# APS: the project's default dashboard JWT authentication.
urlpatterns = [*_patterns('aps', 'aps')]
gas_guard_urlpatterns = _patterns('gas_guard', 'gg', authentication_classes=[GasGuardJWTAuthentication])
