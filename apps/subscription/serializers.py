from rest_framework import serializers


class CreateCheckoutSessionSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=True)
    email = serializers.EmailField(max_length=320, required=True)
    phone = serializers.CharField(max_length=20, required=True)
    address = serializers.CharField(max_length=510, required=True)
