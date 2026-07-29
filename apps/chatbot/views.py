from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
import logging
from .ai import CompanyChatbot


logger = logging.getLogger(__name__)

# Initialize chatbot instance
chatbot = CompanyChatbot()


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
    except Exception:
        logger.exception("Chat endpoint error")
        return JsonResponse({
            'success': False,
            'error': 'Internal server error'
        }, status=500)
