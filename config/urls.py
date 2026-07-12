"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
# from django.views.generic import RedirectView
# from rest_framework import permissions
# from drf_yasg.views import get_schema_view
# from drf_yasg import openapi
from .views import HomePageAPIView


# NOTE: Don't create public API Documentation. Create a private custom API Documentation.
# schema_view = get_schema_view(
#     openapi.Info(
#         title="Alternate Power Solutions API",
#         default_version='v1',
#         description=(
#             "API documentation for Alternate Power Solutions API"
#         ),
#     ),

#     public=True,
#     permission_classes=(permissions.AllowAny,),
# )

urlpatterns = [
    path('admin/', admin.site.urls),
    path('home/', HomePageAPIView.as_view(), name='home'),

    # Config custom apps URLs
    path('blogs/', include('apps.blog.urls')),
    path('chatbot/', include('apps.chatbot.urls')),
    path('container-conversion/', include('apps.container_conversion.urls')),
    path('quote-request/', include('apps.quote_request.urls')),
    path('request-solar-cleaning/', include('apps.request_solar_cleaning.urls')),
    path('subscription/', include('apps.subscription.urls')),
    path('services-projects/', include('apps.services_and_projects.urls')),
    path('dashboard/', include('apps.solar_dashboard.urls')),
    path('fault-detection/', include('apps.fault_detection.urls')),
    path('whatsapp/', include('apps.whatsapp_import.urls')),
    path('weight-scale/', include('apps.weight_scale.urls')),

    # Config URLs for Swagger API Documentation
    # NOTE: Don't create public API Documentation. Create a private custom API Documentation.
    # path('', RedirectView.as_view(url='docs/', permanent=False), name='landing'),
    # path('swagger<format>', schema_view.without_ui(cache_timeout=0), name='schema-json'),  # example path: domain/swagger.json/
    # path('docs/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    # path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc-ui'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
