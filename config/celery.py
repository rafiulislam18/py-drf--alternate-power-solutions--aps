import os
from celery import Celery

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')

# Read Celery config from Django settings (CELERY_ prefix)
app.config_from_object('django.conf:settings', namespace='CELERY')

# Explicitly load tasks from your apps (add this block)
app.autodiscover_tasks([
    'apps.core',          # ← add your app(s) here
    'apps.fault_detection',
    'apps.whatsapp_import',
    'apps.subscription_sheet',
    # 'apps.otherapp',    # if you have more
])

# Optional: debug registered tasks at startup
@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    print("Registered tasks:", list(app.tasks.keys()))
