from django.urls import path

from .views import (
    WhatsAppChatListView,
    WhatsAppJobsExportView,
    WhatsAppMessageBulkMarkView,
    WhatsAppMessageListView,
    WhatsAppMessageMarkView,
)

urlpatterns = [
    path('messages/', WhatsAppMessageListView.as_view(), name='whatsapp-message-list'),
    path('messages/bulk-mark/', WhatsAppMessageBulkMarkView.as_view(), name='whatsapp-message-bulk-mark'),
    path('messages/<int:pk>/mark/', WhatsAppMessageMarkView.as_view(), name='whatsapp-message-mark'),
    path('chats/', WhatsAppChatListView.as_view(), name='whatsapp-chat-list'),
    path('jobs-export/', WhatsAppJobsExportView.as_view(), name='whatsapp-jobs-export'),
]
