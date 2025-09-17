from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
import json
import requests
# import logging

# logger = logging.getLogger(__name__)

class CompanyChatbot:
    def __init__(self):
        # Replace with your actual Grok API key
        self.api_key = settings.CHATBOT_API_KEY
        self.api_url = settings.CHATBOT_API_URL
        
        # Company information - Replace with your actual company data
        self.company_context = """
        Company Name: TechCorp Solutions
        Industry: Software Development
        Services: Web Development, Mobile Apps, Cloud Solutions, AI Integration
        Founded: 2020
        Location: San Francisco, CA
        Working Hours: Monday-Friday 9AM-6PM PST
        Contact Email: contact@techcorp.com
        Contact Phone: +1-555-0123
        
        Key Products:
        1. CloudSync - Enterprise cloud management platform
        2. AppBuilder - No-code mobile app development tool
        3. DataInsight - Business analytics dashboard
        
        Pricing:
        - CloudSync: Starting at $99/month
        - AppBuilder: $49/month for individuals, $199/month for teams
        - DataInsight: Custom pricing based on data volume
        
        FAQ:
        - We offer 24/7 customer support for enterprise clients
        - Free trial available for all products (14 days)
        - We provide custom solutions for enterprise needs
        - Our team has 50+ experienced developers
        """
        
        self.system_prompt = f"""You are a helpful customer service chatbot for TechCorp Solutions. 
        Your ONLY job is to answer questions about the company, its products, services, and related information.
        
        Company Information:
        {self.company_context}
        
        IMPORTANT RULES:
        1. ONLY answer questions related to TechCorp Solutions, its products, services, pricing, or general company information
        2. If asked about anything unrelated to the company, politely decline and redirect to company-related topics
        3. Be professional, friendly, and helpful
        4. If you don't have specific information about something company-related, suggest contacting support
        5. Never make up information about the company
        
        For non-company questions, respond with: "I'm here to help with questions about TechCorp Solutions and our services. How can I assist you with our products or company information?"
        """
    
    def get_response(self, user_message):
        """
        Get response from Grok API with company-specific context
        """
        try:
            if len(user_message) > 500:  # Example limit
                return {
                    'success': False,
                    'response': "Message is too long. Please keep it under 500 characters."
                }
            
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'model': 'grok-3',
                'messages': [
                    {'role': 'system', 'content': self.system_prompt},
                    {'role': 'user', 'content': user_message}
                ],
                'temperature': 0.7,
                'max_tokens': 300
            }
            
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                bot_response = data['choices'][0]['message']['content']
                return {
                    'success': True,
                    'response': bot_response
                }
            else:
                # logger.error(f"Grok API error: {response.status_code} - {response.text}")
                print(response.status_code, response.text)
                return {
                    'success': False,
                    'response': "I'm having trouble connecting right now. Please try again or contact support at contact@techcorp.com"
                }
                
        except Exception as e:
            # logger.error(f"Chatbot error: {str(e)}")
            return {
                'success': False,
                'response': "I encountered an error. Please try again or contact our support team."
            }

# Initialize chatbot instance
chatbot = CompanyChatbot()

# print(chatbot.get_response("signup view code for django that techcorp uses?"))

@csrf_exempt
@require_http_methods(["POST"])
def chat_completions(request):
    """
    API endpoint for chat messages.

    Payload Example:
        {
            'message': 'user's message here'
        }
    """
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return JsonResponse({
                'success': False,
                'error': 'Message cannot be empty'
            }, status=400)
        
        # Get response from chatbot
        response = chatbot.get_response(user_message)
        
        return JsonResponse(response)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        # logger.error(f"Chat endpoint error: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Internal server error'
        }, status=500)
