from rest_framework import serializers

from .models import WhatsAppMessage


class WhatsAppMessageSerializer(serializers.ModelSerializer):
    """Read view of a message for the review page."""

    class Meta:
        model = WhatsAppMessage
        fields = (
            'id', 'sender', 'sent_at', 'text', 'chat_name',
            'marked_as_job', 'marked_as_job_at',
            'exported_to_jobs_sheet', 'exported_to_jobs_sheet_at',
        )
        read_only_fields = fields


class MarkJobSerializer(serializers.Serializer):
    """Toggle a single message's job flag."""
    marked_as_job = serializers.BooleanField()


class BulkMarkJobSerializer(serializers.Serializer):
    """Mark/unmark several messages at once."""
    ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False, max_length=500,
    )
    marked_as_job = serializers.BooleanField()
