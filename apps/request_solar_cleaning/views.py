from django.shortcuts import render
import hashlib
import urllib.parse
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from .models import Client, Request
from .serializers import CreateSolarCleaningCheckoutSerializer

logger = logging.getLogger(__name__)

class CreateSolarCleaningCheckoutSession(APIView):
    def post(self, request):
        serializer = CreateSolarCleaningCheckoutSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        name = serializer.validated_data['name']
        email = serializer.validated_data['email']
        phone = serializer.validated_data['phone']
        address = serializer.validated_data['address']

        try:
            client, _ = Client.objects.get_or_create(
                email=email,
                defaults={'name': name, 'phone': phone}
            )
            req = Request.objects.create(
                client=client,
                address=address,
                paid=False
            )
            payfast_data = self.generate_payfast_data(name, email, req.id)
            signature = self.generate_signature(payfast_data)
            payfast_data['signature'] = signature

            payfast_url = 'https://sandbox.payfast.co.za/eng/process' if settings.PAYFAST_SANDBOX else 'https://www.payfast.co.za/eng/process'

            return Response({
                'url': payfast_url,
                'data': payfast_data,
                'method': 'POST'
            })
        except Exception as e:
            logger.error(f"Error creating PayFast checkout session: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def generate_payfast_data(self, name, email, request_id):
        name_parts = name.split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ''
        data = {
            'merchant_id': settings.PAYFAST_MERCHANT_ID,
            'merchant_key': settings.PAYFAST_MERCHANT_KEY,
            'name_first': first_name,
            'name_last': last_name,
            'email_address': email,
            'm_payment_id': str(request_id),
            'amount': '99.00',  # Example price for solar cleaning
            'item_name': 'Solar Cleaning',
            'item_description': 'One-time solar panel cleaning service',
            'email_confirmation': '1',
            'confirmation_address': email,
            'return_url': settings.PAYFAST_RETURN_URL,
            'cancel_url': settings.PAYFAST_CANCEL_URL,
            'notify_url': settings.PAYFAST_SOLAR_CLEANING_NOTIFY_URL,
        }
        return data

    def generate_signature(self, data):
        param_string = ''
        for key in sorted(data.keys()):
            value = str(data[key]) if data[key] is not None else ''
            param_string += f'{key}={urllib.parse.quote_plus(value)}&'
        param_string = param_string[:-1]
        # Only append passphrase if it’s non-empty
        if hasattr(settings, 'PAYFAST_PASSPHRASE') and settings.PAYFAST_PASSPHRASE:
            param_string += f'&passphrase={urllib.parse.quote_plus(settings.PAYFAST_PASSPHRASE)}'
            logger.info(f"Passphrase added: {settings.PAYFAST_PASSPHRASE}")
        logger.info(f"Checkout param string: {param_string}")
        signature = hashlib.md5(param_string.encode('utf-8')).hexdigest()
        logger.info(f"Generated signature: {signature}")
        return signature

@csrf_exempt
def payfast_notify(request):
    print("Step 1: Entered payfast_notify")
    if request.method != 'POST':
        print("Step 2: Not a POST request")
        return HttpResponse(status=405)
    try:
        post_data = request.POST.dict()
        print(f"Step 3: Raw POST data: {post_data}")

        # Signature verification
        result_signature = verify_payfast_signature(post_data.copy())
        print(f"Step 4: Signature verification result: {result_signature}")
        if not result_signature:
            print("Step 5: Signature verification failed")
            return HttpResponse(status=400)

        # IP verification
        result_ip = verify_payfast_ip(request)
        print(f"Step 6: IP verification result: {result_ip}")
        if not result_ip:
            print("Step 7: IP verification failed")
            return HttpResponse(status=401)

        # Amount check
        amount_gross = post_data.get('amount_gross')
        print(f"Step 8: amount_gross from PayFast: {amount_gross}")
        if amount_gross != '99.00':
            print(f"Step 9: Amount mismatch: expected 99.00, got {amount_gross}")
            return HttpResponse(status=400)

        # Payment status and request ID
        payment_status = post_data.get('payment_status')
        request_id = post_data.get('m_payment_id')
        token = post_data.get('token')
        print(f"Step 10: payment_status={payment_status}, request_id={request_id}, token={token}")
        if not request_id:
            print("Step 11: No request ID in PayFast notification")
            return HttpResponse(status=400)

        # Fetch request object
        try:
            req = Request.objects.get(id=request_id)
            print(f"Step 12: Found Request object: {req}")
        except Request.DoesNotExist:
            print(f"Step 13: Request {request_id} not found")
            return HttpResponse(status=404)

        # Update payment status
        if payment_status == 'COMPLETE':
            req.paid = True
            req.payfast_token = token
            req.payfast_payment_id = post_data.get('pf_payment_id')
            req.save()
            print(f"Step 14: Solar cleaning request {request_id} marked as paid")
        elif payment_status == 'FAILED':
            print(f"Step 15: Payment failed for request {request_id}")

        print("Step 16: Finished processing PayFast ITN")
        return HttpResponse(status=200)
    except Exception as e:
        print(f"Step 17: Error processing PayFast ITN: {str(e)}")
        return HttpResponse(status=500)

@csrf_exempt
def payfast_return(request):
    return JsonResponse({'status': 'success', 'message': 'Payment received. We will contact you soon.'})

@csrf_exempt
def payfast_cancel(request):
    return JsonResponse({'status': 'cancelled', 'message': 'Payment was cancelled.'})

def verify_payfast_signature(post_data):
    received_signature = post_data.pop('signature', None)
    if not received_signature:
        logger.error("No signature received in ITN data")
        return False
    param_string = ''
    for key in sorted(post_data.keys()):
        value = str(post_data[key]) if post_data[key] is not None else ''
        encoded_value = urllib.parse.quote_plus(value)
        param_string += f'{key}={encoded_value}&'
        logger.info(f"Field: {key}={encoded_value}")
    param_string = param_string[:-1]
    # Only append passphrase if it’s non-empty
    if hasattr(settings, 'PAYFAST_PASSPHRASE') and settings.PAYFAST_PASSPHRASE:
        param_string += f'&passphrase={urllib.parse.quote_plus(settings.PAYFAST_PASSPHRASE)}'
        logger.info(f"Passphrase added: {settings.PAYFAST_PASSPHRASE}")
    calculated_signature = hashlib.md5(param_string.encode('utf-8')).hexdigest()
    logger.info(f"ITN param string: {param_string}")
    print(f"ITN param string: {param_string}")
    logger.info(f"Received signature: {received_signature}")
    print(f"Received signature: {received_signature}")
    logger.info(f"Calculated signature: {calculated_signature}")
    print(f"Calculated signature: {calculated_signature}")
    return calculated_signature == received_signature.lower()

def verify_payfast_ip(request):
    valid_ips = [
        '41.74.179.130', '41.74.179.150', '41.74.179.251', '41.74.179.210',
        '41.74.179.211', '41.74.179.212', '41.74.179.213', '41.74.179.214',
        '41.74.179.215', '41.74.179.216', '41.74.179.217', '41.74.179.218',
        '41.74.179.194', '41.74.179.164',
    ]
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    client_ip = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR')
    if settings.PAYFAST_SANDBOX:
        return True
    return client_ip in valid_ips
