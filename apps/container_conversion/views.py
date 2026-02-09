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
        """
        Create a new service request
        """
        serializer = ServiceRequestSerializer(data=request.data)

        if serializer.is_valid():
            service_request = serializer.save()
            return Response(
                {
                    "message": "Service request submitted successfully. We'll review your request and get back to you very soon.",
                    "request_id": service_request.id,
                    "data": ServiceRequestSerializer(service_request).data,
                },
                status=status.HTTP_201_CREATED
            )

        return Response(
            {
                "message": "Failed to submit service request. Please check the form and try again.",
                "errors": serializer.errors,
            },
            status=status.HTTP_400_BAD_REQUEST
        )
