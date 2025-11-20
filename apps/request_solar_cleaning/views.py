from django.shortcuts import render
import hashlib
import socket
import urllib.parse
from urllib.parse import urlparse as url_parse
import logging
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
from .models import Client, Request
from .serializers import CreateSolarCleaningCheckoutSerializer

logger = logging.getLogger(__name__)

@method_decorator(csrf_exempt, name='dispatch')
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
            # signature = self.generate_signature(payfast_data, settings.PAYFAST_PASSPHRASE if hasattr(settings, 'PAYFAST_PASSPHRASE') else '')
            signature = self.generate_signature(payfast_data, settings.PAYFAST_PASSPHRASE)
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
    print("Step 1: Entered payfast_notify")
    if request.method != 'POST':
        print("Step 2: Not a POST request")
        return HttpResponse(status=405)
    try:
        post_data = request.POST.dict()
        print(f"Step 3: Raw POST data: {post_data}")

        ## Signature verification
        # result_signature = verify_payfast_signature(post_data.copy())
        # print(f"Step 4: Signature verification result: {result_signature}")
        # if not result_signature:
            # print("Step 5: Signature verification failed")
            # return HttpResponse(status=400)

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
                    <h2 style="color: #D96F32; text-align: center; border-bottom: 2px solid #D96F32; padding-bottom: 10px;">New Solar Cleaning Payment</h2>
                    <div style="background-color: white; padding: 20px; border-radius: 5px; margin-top: 20px;">
                        <p><strong style="color: #D96F32;">Customer Name:</strong> {req.client.name}</p>
                        <p><strong style="color: #D96F32;">Customer Email:</strong> {req.client.email}</p>
                        <p><strong style="color: #D96F32;">Customer Phone:</strong> {req.client.phone}</p>
                        <p><strong style="color: #D96F32;">Request ID:</strong> {req.id}</p>
                        <p><strong style="color: #D96F32;">Amount Paid:</strong> R99.00</p>
                        <p><strong style="color: #D96F32;">Payment Status:</strong> Completed</p>
                        <p><strong style="color: #D96F32;">Request Created:</strong> {req.created_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
                        <p><strong style="color: #D96F32;">Address:</strong></p>
                        <p style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #D96F32; margin-left: 20px;">
                            {req.address}
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
                subject=f"APS New Solar Cleaning Payment from {req.client.name}",
                body=html_message,
                from_email=settings.EMAIL_HOST_USER,
                to=[settings.EMAIL_HOST_USER],  # Send to self
            )
            email.content_subtype = "html"  # Set content type to HTML
            email.send(fail_silently=True)
            
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
