from django.core.mail import EmailMessage
from django.conf import settings
from rest_framework.views import APIView
from rest_framework import status
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .models import *
from .serializers import *


@method_decorator(csrf_exempt, name='dispatch')
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
            instance = serializer.save()

            # HTML Email Template
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        line-height: 1.6;
                    }}
                </style>
            </head>
            <body>
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa; border-radius: 10px;">
                    <h2 style="color: #D96F32; text-align: center; border-bottom: 2px solid #D96F32; padding-bottom: 10px;">New Quote Request</h2>
                    <div style="background-color: white; padding: 20px; border-radius: 5px; margin-top: 20px;">
                        <p><strong style="color: #D96F32;">Customer Name:</strong> {instance.name}</p>
                        <p><strong style="color: #D96F32;">Customer Phone:</strong> {instance.phone}</p>
                        <p><strong style="color: #D96F32;">Customer Email:</strong> {instance.email}</p>
                        <p><strong style="color: #D96F32;">Selected Service:</strong> {instance.service.title}</p>
                        <p><strong style="color: #D96F32;">Message:</strong></p>
                        <p style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #D96F32; margin-left: 20px;">
                            {instance.message}
                        </p>
                        <p><strong style="color: #D96F32;">Submitted At:</strong> {instance.created_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
                    </div>
                    <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                        <p>This is an automated message from Alter Power Solutions</p>
                    </div>
                </div>
            </body>
            </html>
            """

            # Create email message
            email = EmailMessage(
                subject=f"APS New Quote Request from {instance.name}",
                body=html_message,
                from_email=settings.EMAIL_HOST_USER,
                to=[settings.EMAIL_HOST_USER],
            )
            email.content_subtype = "html"  # Main content is now text/html
            email.send(fail_silently=True)

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
