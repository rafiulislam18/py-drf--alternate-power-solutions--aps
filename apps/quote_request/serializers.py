from rest_framework import serializers
from .models import QuoteRequest, SolarQuoteDetails


class SolarQuoteDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SolarQuoteDetails
        exclude = ('quote_request',)


class QuoteRequestSerializer(serializers.ModelSerializer):
    solar_details = SolarQuoteDetailsSerializer(required=False, allow_null=True)

    class Meta:
        model = QuoteRequest
        fields = '__all__'
        read_only_fields = ('created_at', 'sent_quote')

    def create(self, validated_data):
        solar_details_data = validated_data.pop('solar_details', None)
        quote_request = QuoteRequest.objects.create(**validated_data)
        if solar_details_data:
            SolarQuoteDetails.objects.create(quote_request=quote_request, **solar_details_data)
        return quote_request
