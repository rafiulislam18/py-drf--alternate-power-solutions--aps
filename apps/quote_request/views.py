from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .models import *
from .serializers import *


class QuoteRequestAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = QuoteRequestSerializer(data=request.data)
        if serializer.is_valid():
            # Check if missing fields - name, phone, email, service
            required_fields = ['name', 'phone', 'email', 'service']
            missing_fields = [field for field in required_fields if field not in serializer.validated_data]
            if missing_fields:
                return Response({"error": f"Missing fields: {', '.join(missing_fields)}"}, status=status.HTTP_400_BAD_REQUEST)
            serializer.save()

            # Send email notification

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
