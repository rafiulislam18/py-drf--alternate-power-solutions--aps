"""
Make audit history append-only in PostgreSQL itself: any UPDATE or DELETE on
records_auditlog raises, whoever runs it (the app, the Django shell, psql).
The model refuses too; this also covers raw SQL. Skipped on other databases.

To ever change it on purpose, a database owner has to drop the trigger first.
"""

from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION records_auditlog_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit history can''t be changed or deleted.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS records_auditlog_append_only ON records_auditlog;
CREATE TRIGGER records_auditlog_append_only
    BEFORE UPDATE OR DELETE ON records_auditlog
    FOR EACH ROW EXECUTE FUNCTION records_auditlog_append_only();
"""

BACKWARD = """
DROP TRIGGER IF EXISTS records_auditlog_append_only ON records_auditlog;
DROP FUNCTION IF EXISTS records_auditlog_append_only();
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(FORWARD)


def backward(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(BACKWARD)


class Migration(migrations.Migration):

    dependencies = [
        ('records', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
