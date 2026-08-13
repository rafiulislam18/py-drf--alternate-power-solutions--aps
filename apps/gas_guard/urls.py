"""
Aggregate URL configuration for all Gas Guard sub-apps.

Mounted by the project's ``config/urls.py`` under the ``api/gas-guard/`` prefix,
so e.g. the login endpoint is ``/api/gas-guard/users/login/``. This keeps every
Gas Guard route namespaced away from the APS website's own API routes.
"""
from django.urls import include, path

urlpatterns = [
    path('users/', include('apps.gas_guard.users.urls')),
    path('weight-scale/', include('apps.gas_guard.weight_scale.urls')),
    path('leads/', include('apps.gas_guard.leads.urls')),
    path('subscription/', include('apps.gas_guard.subscription.urls')),
]
