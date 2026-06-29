from django.core.mail import EmailMessage
from django.conf import settings
from django.utils import timezone
from rest_framework import status, generics, pagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ServiceRequest, ContainerProject
from .serializers import ServiceRequestSerializer, ContainerProjectSerializer


class ServiceRequestCreateAPIView(APIView):
    """
    Public API endpoint to create a container conversion service quote request
    """
    permission_classes = [AllowAny]

    def post(self, request):
        """
        Create a new service quote request
        """
        serializer = ServiceRequestSerializer(data=request.data)

        if serializer.is_valid():
            service_request = serializer.save()
            
            # Build additional details section if present
            additional_details_section = ""
            if service_request.additional_details:
                additional_details_section = f'''
                            <div style="margin-top: 15px;">
                                <strong style="color: #D96F32;">Additional Details:</strong>
                                <div style="background-color: #f8f9fa; padding: 12px; border-left: 4px solid #D96F32; margin-top: 8px;">
                                    {service_request.additional_details}
                                </div>
                            </div>
                '''
            
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
                    .section {{
                        margin-bottom: 20px;
                    }}
                    .section-title {{
                        color: #D96F32;
                        font-weight: bold;
                        border-bottom: 2px solid #D96F32;
                        padding-bottom: 10px;
                        margin-bottom: 10px;
                    }}
                    .info-row {{
                        display: flex;
                        justify-content: space-between;
                        padding: 8px 0;
                        border-bottom: 1px solid #e0e0e0;
                    }}
                    .label {{
                        color: #D96F32;
                        font-weight: bold;
                        min-width: 200px;
                    }}
                    .value {{
                        color: #333;
                        word-break: break-word;
                    }}
                </style>
            </head>
            <body>
                <div style="max-width: 700px; margin: 0 auto; padding: 20px; background-color: #f8f9fa; border-radius: 10px;">
                    <h2 style="color: #D96F32; text-align: center; border-bottom: 3px solid #D96F32; padding-bottom: 15px;">
                        🔔 New Container Conversion Quote Request
                    </h2>
                    
                    <div style="background-color: white; padding: 20px; border-radius: 5px; margin-top: 20px;">
                        
                        <!-- Contact Information -->
                        <div class="section">
                            <div class="section-title">Contact Information</div>
                            <div class="info-row">
                                <span class="label">Name:</span>
                                <span class="value">{service_request.first_name} {service_request.last_name}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Email:</span>
                                <span class="value">{service_request.email}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Phone:</span>
                                <span class="value">{service_request.phone}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Company Name:</span>
                                <span class="value">{service_request.company_name or 'Not provided'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Preferred Contact Method:</span>
                                <span class="value">{service_request.get_preferred_contact_method_display()}</span>
                            </div>
                        </div>
                        
                        <!-- Unit Configuration -->
                        <div class="section">
                            <div class="section-title">Unit Configuration</div>
                            <div class="info-row">
                                <span class="label">Unit Type:</span>
                                <span class="value">{service_request.get_unit_type_display()}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Intended Use:</span>
                                <span class="value">{service_request.get_intended_use_display()}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Modular Size:</span>
                                <span class="value">{service_request.get_modular_size_display()}</span>
                            </div>
                        </div>
                        
                        <!-- Optional Extras -->
                        <div class="section">
                            <div class="section-title">Optional Extras</div>
                            <div class="info-row">
                                <span class="label">Ablution Unit:</span>
                                <span class="value">{'Yes ✓' if service_request.ablution_unit else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Electrical Installation:</span>
                                <span class="value">{'Yes ✓' if service_request.electrical_installation else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Plumbing Installation:</span>
                                <span class="value">{'Yes ✓' if service_request.plumbing_installation else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Insulation:</span>
                                <span class="value">{'Yes ✓' if service_request.insulation else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Interior Finishes:</span>
                                <span class="value">{'Yes ✓' if service_request.interior_finishes else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Air Conditioning:</span>
                                <span class="value">{'Yes ✓' if service_request.air_conditioning else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Solar / Backup Power:</span>
                                <span class="value">{'Yes ✓' if service_request.solar_backup_power else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Custom Painting / Branding:</span>
                                <span class="value">{'Yes ✓' if service_request.custom_painting_branding else 'No'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Delivery and Installation:</span>
                                <span class="value">{'Yes ✓' if service_request.delivery_and_installation else 'No'}</span>
                            </div>
                        </div>
                        
                        <!-- Project Details -->
                        <div class="section">
                            <div class="section-title">Project Details</div>
                            <div class="info-row">
                                <span class="label">Project Timeframe:</span>
                                <span class="value">{service_request.get_project_timeframe_display() if service_request.project_timeframe else 'Not specified'}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Budget Range:</span>
                                <span class="value">{service_request.get_budget_range_display()}</span>
                            </div>
                            <div class="info-row">
                                <span class="label">Finish Level / Fittings Preference:</span>
                                <span class="value">{service_request.get_finish_level_display()}</span>
                            </div>
                        </div>
                        
                        <!-- Delivery & Details -->
                        <div class="section">
                            <div class="section-title">Delivery & Additional Details</div>
                            <div class="info-row">
                                <span class="label">Delivery Address:</span>
                            </div>
                            <div style="background-color: #f8f9fa; padding: 12px; border-left: 4px solid #D96F32; margin: 10px 0;">
                                {service_request.transport_or_export_address}
                            </div>
                            {additional_details_section}
                        </div>
                        
                        <!-- Footer -->
                        <div style="margin-top: 30px; padding-top: 20px; border-top: 2px solid #e0e0e0;">
                            <p style="color: #666; font-size: 12px; text-align: center;">
                                <strong>Request ID:</strong> {service_request.id}<br>
                                <strong>Submitted At:</strong> {timezone.localtime(service_request.created_at).strftime('%Y-%m-%d %H:%M:%S')}<br>
                                <em>This is an automated message from Alternate Power Solutions</em>
                            </p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """

            # Send email notification
            try:
                email = EmailMessage(
                    subject=f"APS New Container Conversion Quote Request from {service_request.first_name} {service_request.last_name}",
                    body=html_message,
                    from_email=settings.EMAIL_HOST_USER,
                    to=[settings.EMAIL_RECIPIENT],
                )
                email.content_subtype = "html"
                email.send(fail_silently=True)
            except Exception:
                return Response(
                    {
                        "message": "Quote request submitted successfully, but failed to send notification email to admin. Please contact APS directly to confirm your request with request ID.",
                        "request_id": service_request.id,
                        "data": ServiceRequestSerializer(service_request).data,
                    },
                    status=status.HTTP_201_CREATED
                )
            
            return Response(
                {
                    "message": "Quote request submitted successfully. We'll review your request and get back to you very soon.",
                    "request_id": service_request.id,
                    "data": ServiceRequestSerializer(service_request).data,
                },
                status=status.HTTP_201_CREATED
            )

        return Response(
            {
                "message": "Failed to submit quote request. Please check the form and try again.",
                "errors": serializer.errors,
            },
            status=status.HTTP_400_BAD_REQUEST
        )


class ContainerProjectPagination(pagination.PageNumberPagination):
    """
    Custom pagination for container projects
    """
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class ContainerProjectListView(generics.ListAPIView):
    """
    Public API endpoint to list container conversion projects
    Supports pagination with limit parameter
    """
    queryset = ContainerProject.objects.all().order_by("-completion_date")
    serializer_class = ContainerProjectSerializer
    permission_classes = [AllowAny]
    pagination_class = ContainerProjectPagination


class ContainerProjectDetailView(generics.RetrieveAPIView):
    """
    Public API endpoint to retrieve details of a specific container conversion project
    """
    queryset = ContainerProject.objects.all()
    serializer_class = ContainerProjectSerializer
    permission_classes = [AllowAny]
    lookup_field = "id"
