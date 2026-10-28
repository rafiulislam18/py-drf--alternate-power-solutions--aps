from django.apps import AppConfig


class RecordsTestAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tests.records.testapp'
    label = 'records_testapp'
