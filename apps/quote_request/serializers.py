from rest_framework import serializers
from .models import *


class QuoteRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteRequest
        fields = '__all__'
        read_only_fields = ('created_at', 'sent_quote')
