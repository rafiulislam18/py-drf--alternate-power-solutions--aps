import stripe
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.conf import settings
from .models import Client, Subscription
from .serializers import CreateCheckoutSessionSerializer


stripe.api_key = settings.STRIPE_SECRET_KEY

class CreateCheckoutSession(APIView):
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
            customer = stripe.Customer.create(
                name=name,
                email=email,
                phone=phone,
                address={'line1': address},
            )
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                mode='subscription',
                line_items=[{
                    'price': 'your_stripe_price_id',  # Create recurring price in Stripe dashboard for R99/month
                    'quantity': 1,
                }],
                success_url='http://localhost:3000/success?session_id={CHECKOUT_SESSION_ID}',
                cancel_url='http://localhost:3000/cancel',
                customer=customer.id,
            )

            # Get or create Client
            client = Client.objects.create(
                name=name,
                email=email,
                phone=phone,
                address=address
            )
            client.save()

            # Create or update subscription
            sub = Subscription.objects.get_or_create(client=client)
            sub.stripe_customer_id = customer.id
            sub.save()
            return Response({'url': session.url})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META['HTTP_STRIPE_SIGNATURE']
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        return HttpResponse(status=400)

    if event.type == 'checkout.session.completed':
        session = event.data.object
        customer_id = session.customer
        sub_id = session.subscription
        sub = Subscription.objects.get_or_create(stripe_customer_id=customer_id)
        sub.stripe_subscription_id = sub_id
        sub.is_active = True
        sub.save()
        # Send welcome email here (use django mail)

    elif event.type == 'invoice.paid':
        # Monthly payment success
        sub_id = event.data.object.subscription
        sub = Subscription.objects.get_or_create(stripe_subscription_id=sub_id)
        sub.subscription_length += 1
        if (sub.subscription_length % 12) == 0:
            sub.call_out_balance = 2
        sub.save()

    elif event.type == 'customer.subscription.deleted':
        sub_id = event.data.object.id
        sub = Subscription.objects.get_or_create(stripe_subscription_id=sub_id)
        sub.is_active = False
        sub.save()

    else:
        print(f"Unhandled event type: {event.type}")

    return HttpResponse(status=200)
