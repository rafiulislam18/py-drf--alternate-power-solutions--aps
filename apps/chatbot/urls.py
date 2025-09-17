from django.urls import path
from .views import chat_completions


urlpatterns = [
    path('chat-completions/', chat_completions),
]
