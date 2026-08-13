from rest_framework import serializers

from .models import DemoBooking


class DemoBookingSerializer(serializers.ModelSerializer):
    """
    Validates a public demo enquiry from the landing page.

    ``handled`` and timestamps are server-owned; the client only supplies the
    contact details. Field names line up with the React form.
    """

    class Meta:
        model = DemoBooking
        fields = ['id', 'name', 'email', 'company', 'sites', 'message', 'created_at']
        read_only_fields = ['id', 'created_at']
