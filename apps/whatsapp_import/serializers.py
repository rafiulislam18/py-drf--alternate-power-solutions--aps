from rest_framework import serializers

from apps.jobs.visibility import can_see_job

from .models import WhatsAppMessage


class WhatsAppMessageSerializer(serializers.ModelSerializer):
    """Read view of a message for the review page. ``job`` is the dashboard job it
    became ({id, job_id, can_open}), or null (not yet, or sent to the old sheet).
    ``can_open`` is false for a job the viewer can't see: the number shows, no link."""

    job = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppMessage
        fields = (
            'id', 'sender', 'sent_at', 'text', 'chat_name',
            'marked_as_job', 'marked_as_job_at',
            'exported_to_jobs_sheet', 'exported_to_jobs_sheet_at',
            'dismissed', 'dismissed_at', 'job',
        )
        read_only_fields = fields

    def get_job(self, message):
        job = message.job
        if not job or job.in_trash:
            return None
        request = self.context.get('request')
        return {'id': job.pk, 'job_id': job.job_id,
                'can_open': request is not None and can_see_job(request.user, job)}


class MarkJobSerializer(serializers.Serializer):
    """Toggle a single message's job flag."""
    marked_as_job = serializers.BooleanField()


class BulkMarkJobSerializer(serializers.Serializer):
    """Mark/unmark several messages at once."""
    ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False, max_length=500,
    )
    marked_as_job = serializers.BooleanField()


class DismissSerializer(serializers.Serializer):
    """Toggle a single message's 'not a job' dismissal."""
    dismissed = serializers.BooleanField()


class BulkDismissSerializer(serializers.Serializer):
    """Dismiss/un-dismiss several messages at once."""
    ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False, max_length=500,
    )
    dismissed = serializers.BooleanField()
