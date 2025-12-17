"""
Integration tests for chatbot API endpoints.

Tests:
- Chat completions endpoint
- Message validation
- Response format
- Error handling
"""

import pytest
from rest_framework import status
from unittest.mock import patch, MagicMock
import json


pytestmark = pytest.mark.django_db


class TestChatCompletionsEndpoint:
    """Test cases for chat completions API endpoint."""
    
    def test_send_message_success(self, api_client):
        """Test sending a valid message to chatbot."""
        data = {'message': 'What services do you offer?'}
        
        # Mock the chatbot response
        with patch('apps.chatbot.views.chatbot.get_response') as mock_response:
            mock_response.return_value = {
                'success': True,
                'response': 'We offer electrical installation and solar services.'
            }
            
            response = api_client.post('/chatbot/chat-completions/', data, format='json')
        
        assert response.status_code == status.HTTP_200_OK
    
    def test_empty_message_validation(self, api_client):
        """Test that empty messages are rejected."""
        data = {'message': ''}
        response = api_client.post('/chatbot/chat-completions/', data, format='json')
        
        # Endpoint returns JsonResponse, not DRF Response
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_missing_message_field(self, api_client):
        """Test that missing message field returns error."""
        data = {}
        response = api_client.post('/chatbot/chat-completions/', data, format='json')
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_invalid_json(self, api_client):
        """Test handling of invalid JSON."""
        response = api_client.post(
            '/chatbot/chat-completions/',
            'invalid json',
            content_type='application/json'
        )
        
        # Endpoint returns JsonResponse, not DRF Response
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_post_method_required(self, api_client):
        """Test that GET requests are not allowed."""
        response = api_client.get('/chatbot/chat-completions/')
        
        # Should return 405 Method Not Allowed or 404
        assert response.status_code in [status.HTTP_405_METHOD_NOT_ALLOWED, status.HTTP_404_NOT_FOUND]
    
    def test_allow_any_permission(self, api_client):
        """Test that unauthenticated users can access chatbot."""
        with patch('apps.chatbot.views.chatbot.get_response') as mock_response:
            mock_response.return_value = {'success': True, 'response': 'Hello'}
            
            response = api_client.post('/chatbot/chat-completions/', 
                                      {'message': 'Hi'}, 
                                      format='json')
        
        assert response.status_code == status.HTTP_200_OK
    
    def test_whitespace_only_message(self, api_client):
        """Test that whitespace-only messages are handled."""
        data = {'message': '   '}
        response = api_client.post('/chatbot/chat-completions/', data, format='json')
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_long_message(self, api_client):
        """Test handling of long messages."""
        long_message = 'a' * 5000
        data = {'message': long_message}
        
        with patch('apps.chatbot.views.chatbot.get_response') as mock_response:
            mock_response.return_value = {'success': True, 'response': 'Understood'}
            
            response = api_client.post('/chatbot/chat-completions/', data, format='json')
        
        assert response.status_code == status.HTTP_200_OK
