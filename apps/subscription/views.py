import hashlib
import urllib.parse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from .models import Client, Subscription
from .serializers import CreateCheckoutSessionSerializer
import logging
import json

logger = logging.getLogger(__name__)


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
                is_active=False  # Will be activated after payment confirmation
            )
            
            # Generate PayFast payment data
            payfast_data = self.generate_payfast_data(
                name=name,
                email=email,
                subscription_id=subscription.id
            )
            
            # Generate signature
            signature = self.generate_signature(payfast_data)
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
            'billing_date': '',  # Start immediately
            'recurring_amount': '99.00',
            'frequency': '3',  # Monthly
            'cycles': '0',  # Indefinite
            
            # Return URLs
            'return_url': settings.PAYFAST_RETURN_URL,
            'cancel_url': settings.PAYFAST_CANCEL_URL,
            'notify_url': settings.PAYFAST_NOTIFY_URL,
        }
        
        return data
    
    def generate_signature(self, data):
        param_string = ''
        for key in sorted(data.keys()):
            value = str(data[key]) if data[key] is not None else ''
            param_string += f'{key}={urllib.parse.quote_plus(value)}&'
        param_string = param_string[:-1]
        if hasattr(settings, 'PAYFAST_PASSPHRASE') and settings.PAYFAST_PASSPHRASE:
            param_string += f'&passphrase={urllib.parse.quote_plus(settings.PAYFAST_PASSPHRASE)}'
        signature = hashlib.md5(param_string.encode('utf-8')).hexdigest()
        return signature


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
        
        # Verify signature
        if not verify_payfast_signature(post_data):
            logger.error("PayFast signature verification failed")
            print("PayFast signature verification failed")
            return HttpResponse(status=400)
        
        # Verify source IP (PayFast security requirement)
        if not verify_payfast_ip(request):
            logger.error("PayFast IP verification failed")
            print("PayFast IP verification failed")
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
            return HttpResponse(status=400)
        
        # Get subscription
        try:
            subscription = Subscription.objects.get(id=subscription_id)
        except Subscription.DoesNotExist:
            logger.error(f"Subscription {subscription_id} not found")
            print(f"Subscription {subscription_id} not found")
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
    print("Received signature:", received_signature)
    if not received_signature:
        print("No signature received")
        return False
    param_string = ''
    for key in sorted(post_data.keys()):
        value = str(post_data[key]) if post_data[key] is not None else ''
        param_string += f'{key}={urllib.parse.quote_plus(value)}&'
    param_string = param_string[:-1]
    if hasattr(settings, 'PAYFAST_PASSPHRASE') and settings.PAYFAST_PASSPHRASE:
        param_string += f'&passphrase={urllib.parse.quote_plus(settings.PAYFAST_PASSPHRASE)}'
    print("Param string for signature:", param_string)
    calculated_signature = hashlib.md5(param_string.encode('utf-8')).hexdigest()
    print("Calculated signature:", calculated_signature)
    print("Received signature (lower):", received_signature.lower())
    return calculated_signature == received_signature.lower()


def verify_payfast_ip(request):
    """Verify that the request comes from PayFast's IP addresses"""
    # PayFast IP addresses
    valid_ips = [
        '41.74.179.130',
        '41.74.179.150',
        '41.74.179.251',
        '41.74.179.210',
        '41.74.179.211',
        '41.74.179.212',
        '41.74.179.213',
        '41.74.179.214',
        '41.74.179.215',
        '41.74.179.216',
        '41.74.179.217',
        '41.74.179.218',
        # Sandbox IPs
        '41.74.179.194',
        '41.74.179.164',
    ]
    
    # Get client IP
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(',')[0]
    else:
        client_ip = request.META.get('REMOTE_ADDR')
    
    # In development/sandbox mode, you might want to skip IP validation
    if settings.PAYFAST_SANDBOX:
        return True
    
    return client_ip in valid_ips
