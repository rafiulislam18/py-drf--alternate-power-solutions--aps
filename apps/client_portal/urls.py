from django.urls import path

from . import chat_views, staff_views, views

urlpatterns = [
    path('me/', views.MeView.as_view(), name='client-portal-me'),
    path('sites/', views.SiteListCreateView.as_view(), name='client-portal-sites'),
    path('sites/<int:pk>/', views.SiteDetailView.as_view(), name='client-portal-site-detail'),
    path('subscriptions/', views.SubscriptionListView.as_view(), name='client-portal-subscriptions'),
    path('subscriptions/payments/', views.SubscriptionPaymentsView.as_view(), name='client-portal-subscription-payments'),
    path('subscriptions/cancel/', views.SubscriptionCancelView.as_view(), name='client-portal-subscription-cancel'),
    path('tickets/summary/', views.TicketSummaryView.as_view(), name='client-portal-ticket-summary'),
    path('tickets/unread/', views.TicketUnreadView.as_view(), name='client-portal-ticket-unread'),
    path('tickets/', views.TicketListCreateView.as_view(), name='client-portal-tickets'),
    path('tickets/<int:pk>/', views.ClientTicketDetailView.as_view(), name='client-portal-ticket-detail'),
    path('tickets/<int:pk>/messages/', chat_views.ClientTicketMessagesView.as_view(),
         name='client-portal-ticket-messages'),
    # Staff (dashboard admins): every client's tickets.
    path('staff/tickets/summary/', staff_views.StaffTicketSummaryView.as_view(), name='client-portal-staff-ticket-summary'),
    path('staff/tickets/unread/', staff_views.StaffTicketUnreadView.as_view(), name='client-portal-staff-ticket-unread'),
    path('staff/tickets/', staff_views.StaffTicketListView.as_view(), name='client-portal-staff-tickets'),
    path('staff/tickets/<int:pk>/', staff_views.StaffTicketDetailView.as_view(), name='client-portal-staff-ticket-detail'),
    path('staff/tickets/<int:pk>/messages/', chat_views.StaffTicketMessagesView.as_view(),
         name='client-portal-staff-ticket-messages'),
]
