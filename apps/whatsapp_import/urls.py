from django.urls import path

from .views import (
    WhatsAppChatListView,
    WhatsAppJobsExportView,
    WhatsAppMessageBulkDismissView,
    WhatsAppMessageBulkMarkView,
    WhatsAppMessageDismissView,
    WhatsAppMessageListView,
    WhatsAppMessageMarkView,
    WhatsAppStatusCountsView,
)

urlpatterns = [
    path('messages/', WhatsAppMessageListView.as_view(), name='whatsapp-message-list'),
    path('messages/bulk-mark/', WhatsAppMessageBulkMarkView.as_view(), name='whatsapp-message-bulk-mark'),
    path('messages/bulk-dismiss/', WhatsAppMessageBulkDismissView.as_view(), name='whatsapp-message-bulk-dismiss'),
    path('messages/<int:pk>/mark/', WhatsAppMessageMarkView.as_view(), name='whatsapp-message-mark'),
    path('messages/<int:pk>/dismiss/', WhatsAppMessageDismissView.as_view(), name='whatsapp-message-dismiss'),
    path('chats/', WhatsAppChatListView.as_view(), name='whatsapp-chat-list'),
    path('counts/', WhatsAppStatusCountsView.as_view(), name='whatsapp-status-counts'),
    path('jobs-export/', WhatsAppJobsExportView.as_view(), name='whatsapp-jobs-export'),
]
