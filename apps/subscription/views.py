import hashlib
import socket
import urllib.parse
from urllib.parse import urlparse as url_parse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.core.mail import EmailMessage
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from collections import OrderedDict
from .models import Client, Subscription
from .serializers import CreateCheckoutSessionSerializer
import logging
import json

logger = logging.getLogger(__name__)

@method_decorator(csrf_exempt, name='dispatch')
class CreatePayFastCheckoutSession(APIView):
    def post(self, request):
        # Validate request data using serializer
        serializer = CreateCheckoutSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Get validated data
        name = serializer.validated_data['name']
        email = serializer.validated_data['email']
        phone = serializer.validated_data['phone']
        inverter_type = serializer.validated_data['inverterType']
        address = serializer.validated_data['address']

        try:
            # Get or create Client
            client, created = Client.objects.get_or_create(
                email=email,
                defaults={
                    'name': name,
                    'phone': phone
                }
            )
            
            # Create subscription record (will be activated after payment confirmation)
            subscription = Subscription.objects.create(
                client=client,
                address=address,
                inverter_type=inverter_type,
                is_active=False  # Will be activated after payment confirmation
            )
            
            # Generate PayFast payment data
            payfast_data = self.generate_payfast_data(
                name=name,
                email=email,
                subscription_id=subscription.id
            )
            
            # Generate signature
            # signature = self.generate_signature(payfast_data, settings.PAYFAST_PASSPHRASE if hasattr(settings, 'PAYFAST_PASSPHRASE') else '')
            signature = self.generate_signature(payfast_data, settings.PAYFAST_PASSPHRASE)
            payfast_data['signature'] = signature
            
            # PayFast checkout URL
            if settings.PAYFAST_SANDBOX:
                payfast_url = 'https://sandbox.payfast.co.za/eng/process'
            else:
                payfast_url = 'https://www.payfast.co.za/eng/process'
            
            return Response({
                'url': payfast_url,
                'data': payfast_data,
                'method': 'POST'
            })
            
        except Exception as e:
            logger.error(f"Error creating PayFast checkout session: {str(e)}")
            print(f"Error creating PayFast checkout session: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    def generate_payfast_data(self, name, email, subscription_id):
        """Generate PayFast payment form data"""
        
        # Split name into first and last name
        name_parts = name.split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ''
        
        data = {
            # Merchant details
            'merchant_id': settings.PAYFAST_MERCHANT_ID,
            'merchant_key': settings.PAYFAST_MERCHANT_KEY,
            
            # Buyer details
            'name_first': first_name,
            'name_last': last_name,
            'email_address': email,
            
            # Transaction details
            'm_payment_id': str(subscription_id),
            'amount': '99.00',  # R99 per month
            'item_name': 'Monthly Subscription',
            'item_description': 'Recurring monthly subscription',
            
            # Transaction options
            'email_confirmation': '1',
            'confirmation_address': email,
            
            # Recurring billing
            'subscription_type': '1',  # Recurring subscription
            'recurring_amount': '99.00',
            'frequency': '3',  # Monthly
            'cycles': '0',  # Indefinite
            
            # Return URLs
            'return_url': settings.PAYFAST_RETURN_URL,
            'cancel_url': settings.PAYFAST_CANCEL_URL,
            'notify_url': settings.PAYFAST_NOTIFY_URL,
        }
        
        return data

    def generate_signature(self, dataArray, passPhrase = ''):
        payload = ""
        # 2) sort by key
        ordered_dataArray = OrderedDict(sorted(dataArray.items(), key=lambda kv: kv[0]))
        for key in ordered_dataArray:
            # Get all the data from Payfast and prepare parameter string
            payload += key + "=" + urllib.parse.quote_plus(ordered_dataArray[key].replace("+", " ")) + "&"
        # After looping through, cut the last & or append your passphrase
        payload = payload[:-1]
        if passPhrase != '':
            payload += f"&passphrase={passPhrase}"
        return hashlib.md5(payload.encode()).hexdigest()

@csrf_exempt
def payfast_notify(request):
    """
    PayFast ITN (Instant Transaction Notification) handler
    This is called by PayFast to confirm payment status
    """
    if request.method != 'POST':
        return HttpResponse(status=405)
    
    try:
        # Get POST data
        post_data = request.POST.dict()
        
        # Log the notification for debugging
        logger.info(f"PayFast ITN received: {post_data}")
        print("PayFast ITN received:", post_data)
        
        ## Verify signature
        # if not verify_payfast_signature(post_data):
            # logger.error("PayFast signature verification failed")
            # print("PayFast signature verification failed")
            # return HttpResponse(status=400)
        
        # Verify source IP (PayFast security requirement)
        if not verify_payfast_ip(request):
            logger.error(f"PayFast IP verification failed: {json.dumps(post_data, indent=4)}")
            print("PayFast IP verification failed:")

            # Send email notification
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
            </head>
            <body>
                <h2 style="color: #D96F32;">APS PayFast IP Verification Failed</h2>
                <p>The PayFast IP verification has failed. Please investigate.</p>
                <p><strong>Received Data:</strong></p>
                <pre style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; border: 1px solid #ddd;">{json.dumps(post_data, indent=4)}</pre>
            </body>
            </html>
            """
            email = EmailMessage(
                subject=f"APS PayFast IP Verification Failed",
                body=html_message,
                from_email=settings.EMAIL_HOST_USER,
                to=[settings.EMAIL_HOST_USER],  # Send to self
            )
            email.content_subtype = "html"  # Set content type to HTML
            email.send(fail_silently=True)

            return HttpResponse(status=401)
        
        # Verify amount
        if post_data.get('amount_gross') != '99.00':
            logger.error(f"Amount mismatch: expected 99.00, got {post_data.get('amount_gross')}")
            print(f"Amount mismatch: expected 99.00, got {post_data.get('amount_gross')}")

            return HttpResponse(status=400)
        
        # Get payment status
        payment_status = post_data.get('payment_status')
        subscription_id = post_data.get('m_payment_id')
        token = post_data.get('token')  # PayFast token for recurring payments
        
        if not subscription_id:
            logger.error("No subscription ID in PayFast notification")
            print("No subscription ID in PayFast notification")

            # Send email notification
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
            </head>
            <body>
                <h2 style="color: #D96F32;">APS PayFast Missing Subscription ID</h2>
                <p>The PayFast notification is missing the subscription ID. Please investigate.</p>
                <p><strong>Received Data:</strong></p>
                <pre style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; border: 1px solid #ddd;">{json.dumps(post_data, indent=4)}</pre>
            </body>
            </html>
            """
            email = EmailMessage(
                subject=f"APS PayFast Missing Subscription ID",
                body=html_message,
                from_email=settings.EMAIL_HOST_USER,
                to=[settings.EMAIL_HOST_USER],  # Send to self
            )
            email.content_subtype = "html"  # Set content type to HTML
            email.send(fail_silently=True)

            return HttpResponse(status=400)
        
        # Get subscription
        try:
            subscription = Subscription.objects.get(id=subscription_id)
        except Subscription.DoesNotExist:
            logger.error(f"Subscription {subscription_id} not found")
            print(f"Subscription {subscription_id} not found")

            # Send email notification
            html_message = f"""
            <!DOCTYPE html>
            <html>
            <head>
            </head>
            <body>
                <h2 style="color: #D96F32;">APS PayFast Subscription Not Found</h2>
                <p>The subscription with ID {subscription_id} was not found. Please investigate.</p>
                <p><strong>Received Data:</strong></p>
                <pre style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; border: 1px solid #ddd;">{json.dumps(post_data, indent=4)}</pre>
            </body>
            </html>
            """
            email = EmailMessage(
                subject=f"APS PayFast Subscription Not Found",
                body=html_message,
                from_email=settings.EMAIL_HOST_USER,
                to=[settings.EMAIL_HOST_USER],  # Send to self
            )
            email.content_subtype = "html"  # Set content type to HTML
            email.send(fail_silently=True)
            
            return HttpResponse(status=404)
        
        # Handle payment status
        if payment_status == 'COMPLETE':
            # Payment successful
            subscription.is_active = True
            subscription.payfast_token = token  # Store token for subscription management
            
            # Increment subscription length for recurring payments
            if post_data.get('item_name') == 'Subscription Payment':
                subscription.subscription_length += 1
                
                # Add call out balance every 12 months
                if (subscription.subscription_length % 12) == 0:
                    subscription.call_out_balance = 2
            
            subscription.save()

            # Send notification email to self
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
                    <h2 style="color: #D96F32; text-align: center; border-bottom: 2px solid #D96F32; padding-bottom: 10px;">New Subscription Payment</h2>
                    <div style="background-color: white; padding: 20px; border-radius: 5px; margin-top: 20px;">
                        <p><strong style="color: #D96F32;">Customer Name:</strong> {subscription.client.name}</p>
                        <p><strong style="color: #D96F32;">Customer Email:</strong> {subscription.client.email}</p>
                        <p><strong style="color: #D96F32;">Customer Phone:</strong> {subscription.client.phone}</p>
                        <p><strong style="color: #D96F32;">Subscription ID:</strong> {subscription.id}</p>
                        <p><strong style="color: #D96F32;">Amount Paid:</strong> R99.00</p>
                        <p><strong style="color: #D96F32;">Payment Status:</strong> Completed</p>
                        <p><strong style="color: #D96F32;">Subscription Length:</strong> {subscription.subscription_length} month{'s' if subscription.subscription_length > 1 else ''}</p>
                        <p><strong style="color: #D96F32;">Subscription Start:</strong> {subscription.created_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
                        <p><strong style="color: #D96F32;">Inverter Type:</strong> {subscription.inverter_type}</p>
                        <p><strong style="color: #D96F32;">Address:</strong></p>
                        <p style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #D96F32; margin-left: 20px;">
                            {subscription.address}
                        </p>
                    </div>
                    <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                        <p>This is an automated message from Alternate Power Solutions</p>
                    </div>
                </div>
            </body>
            </html>
            """

            # Create and send email
            email = EmailMessage(
                subject=f"APS New Subscription Payment from {subscription.client.name}",
                body=html_message,
                from_email=settings.EMAIL_HOST_USER,
                to=[settings.EMAIL_HOST_USER],  # Send to self
            )
            email.content_subtype = "html"  # Set content type to HTML
            email.send(fail_silently=True)
            
            # Send welcome/confirmation email here
            logger.info(f"Subscription {subscription_id} activated successfully")
            print(f"Subscription {subscription_id} activated successfully")
            
        elif payment_status == 'CANCELLED':
            # Subscription cancelled
            subscription.is_active = False
            subscription.save()
            logger.info(f"Subscription {subscription_id} cancelled")
            print(f"Subscription {subscription_id} cancelled")
            
        elif payment_status == 'FAILED':
            # Payment failed
            logger.warning(f"Payment failed for subscription {subscription_id}")
            print(f"Payment failed for subscription {subscription_id}")
            
        return HttpResponse(status=200)
        
    except Exception as e:
        logger.error(f"Error processing PayFast ITN: {str(e)}")
        print(f"Error processing PayFast ITN: {str(e)}")
        return HttpResponse(status=500)


@csrf_exempt
def payfast_return(request):
    """
    Handle return from PayFast after payment
    This is where users are redirected after completing payment
    """
    # You can customize this based on your frontend needs
    return JsonResponse({
        'status': 'success',
        'message': 'Payment processing. You will receive a confirmation email shortly.'
    })


@csrf_exempt
def payfast_cancel(request):
    """
    Handle cancellation from PayFast
    This is where users are redirected if they cancel payment
    """
    return JsonResponse({
        'status': 'cancelled',
        'message': 'Payment was cancelled.'
    })


def verify_payfast_signature(post_data):
    received_signature = post_data.pop('signature', None)
    if not received_signature:
        logger.error("No signature received in ITN data")
        return False
    
    # Filter out blank/null values per docs
    post_data = {k: v for k, v in post_data.items() if v is not None and str(v).strip() != ''}
    
    param_string = ''
    for key in sorted(post_data.keys()):
        value = str(post_data[key])
        encoded_value = urllib.parse.quote_plus(value.replace("+", " "))
        param_string += f'{key}={encoded_value}&'
        logger.info(f"Field: {key}={encoded_value}")
    
    param_string = param_string[:-1]
    
    if hasattr(settings, 'PAYFAST_PASSPHRASE') and settings.PAYFAST_PASSPHRASE and settings.PAYFAST_PASSPHRASE != '':
        param_string += f'&passphrase={settings.PAYFAST_PASSPHRASE}'
        logger.info(f"Passphrase added: {settings.PAYFAST_PASSPHRASE}")
    
    calculated_signature = hashlib.md5(param_string.encode()).hexdigest()
    logger.info(f"ITN param string: {param_string}")
    print(f"ITN param string: {param_string}")
    logger.info(f"Received signature: {received_signature}")
    print(f"Received signature: {received_signature}")
    logger.info(f"Calculated signature: {calculated_signature}")
    print(f"Calculated signature: {calculated_signature}")
    
    return calculated_signature == received_signature.lower()


def verify_payfast_ip(request):
    """Verify that the request comes from PayFast's IP addresses"""
    valid_hosts = [
    'www.payfast.co.za',
    'sandbox.payfast.co.za',
    'w1w.payfast.co.za',
    'w2w.payfast.co.za',
    ]
    valid_ips = []

    for item in valid_hosts:
        ips = socket.gethostbyname_ex(item)
        if ips:
            for ip in ips:
                if ip:
                    valid_ips.append(ip)

    # Remove duplicates from array
    clean_valid_ips = []
    for item in valid_ips:
        # Iterate through each variable to create one list
        if isinstance(item, list):
            for prop in item:
                if prop not in clean_valid_ips:
                    clean_valid_ips.append(prop)
        else:
            if item not in clean_valid_ips:
                clean_valid_ips.append(item)

    # Security Step 3, check if referrer is valid
    if url_parse(request.headers.get("Referer")).hostname not in clean_valid_ips:
        return False
    else:
        return True 
