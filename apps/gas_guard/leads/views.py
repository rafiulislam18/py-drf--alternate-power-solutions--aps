import logging

from django.conf import settings
from django.core.mail import EmailMessage
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DemoBooking
from .serializers import DemoBookingSerializer

logger = logging.getLogger(__name__)


class DemoBookingCreateView(APIView):
    """
    POST: record a public "book a demo" enquiry and email the sales inbox.

    Open to anyone (no auth) — it's a marketing lead form. The booking is
    persisted first; the alert email is best-effort so a mail hiccup never
    costs us the lead or shows the visitor an error.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = DemoBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        booking = serializer.save()
        logger.info(f"Demo booking received: {booking}")
        self._notify(booking)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _notify(self, booking):
        recipient = getattr(settings, 'EMAIL_RECIPIENT', None)
        if not recipient:
            logger.warning(
                'EMAIL_RECIPIENT is not set — skipping demo booking alert '
                f'email for {booking.email}'
            )
            return

        html_message = _booking_email_html(booking)
        try:
            email = EmailMessage(
                subject=f'New Gas Guard demo request — {booking.name}',
                body=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
                # Reply straight to the prospect from the sales inbox.
                reply_to=[booking.email],
            )
            email.content_subtype = 'html'
            email.send(fail_silently=True)
            logger.info(f"Demo booking alert emailed to {recipient}")
        except Exception as e:
            # Never fail the request over a mail problem — the lead is saved.
            logger.error(f"Error sending demo booking notification email: {e}")


def _booking_email_html(booking):
    """Simple HTML alert body for a new demo enquiry."""
    return f"""\
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto;
                border: 1px solid #e5e7eb; border-radius: 12px; overflow: hidden;">
      <div style="background: #0c0e12; color: #fff; padding: 20px 24px;">
        <h2 style="margin: 0; font-size: 18px;">New demo request</h2>
        <p style="margin: 4px 0 0; color: #9aa4b2; font-size: 13px;">
          From the Gas Guard landing page
        </p>
      </div>
      <div style="padding: 24px;">
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
          <tr><td style="padding: 6px 0; color: #6b7280; width: 110px;">Name</td>
              <td style="padding: 6px 0;"><strong>{booking.name}</strong></td></tr>
          <tr><td style="padding: 6px 0; color: #6b7280;">Email</td>
              <td style="padding: 6px 0;">
                <a href="mailto:{booking.email}">{booking.email}</a></td></tr>
          <tr><td style="padding: 6px 0; color: #6b7280;">Company</td>
              <td style="padding: 6px 0;">{booking.company or '&mdash;'}</td></tr>
          <tr><td style="padding: 6px 0; color: #6b7280;">Sites</td>
              <td style="padding: 6px 0;">{booking.sites or '&mdash;'}</td></tr>
        </table>
        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #e5e7eb;">
          <div style="color: #6b7280; font-size: 13px; margin-bottom: 6px;">Message</div>
          <div style="white-space: pre-wrap; font-size: 14px;">{booking.message or '&mdash;'}</div>
        </div>
      </div>
      <div style="background: #f9fafb; padding: 14px 24px; color: #9ca3af; font-size: 12px;">
        Gas Guard &mdash; a product of APS
      </div>
    </div>
    """
