from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ServiceRequest
from .serializers import ServiceRequestSerializer


class ServiceRequestCreateAPIView(APIView):
    """
    Public API endpoint to create a container conversion service request
    """

    permission_classes = []  # Public endpoint

    def post(self, request):
        serializer = ServiceRequestSerializer(data=request.data)

        if serializer.is_valid():
            quote = serializer.save()
            return Response(
                {
                    "message": "Quote request submitted successfully.",
                    "quote_id": quote.id,
                },
                status=status.HTTP_201_CREATED
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
