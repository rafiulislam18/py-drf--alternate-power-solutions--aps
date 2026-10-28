from django.apps import AppConfig


class RecordsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.records'
    verbose_name = 'Trash & audit history'
