from django.urls import path

from . import views

urlpatterns = [
    path('', views.JobListView.as_view(), name='jobs'),
    path('options/', views.JobOptionsView.as_view(), name='job-options'),
    path('settings/', views.JobSettingsView.as_view(), name='job-settings'),
    path('categories/', views.JobCategoryListView.as_view(), name='job-categories'),
    path('categories/<int:pk>/', views.JobCategoryDetailView.as_view(), name='job-category-detail'),
    path('<int:pk>/', views.JobDetailView.as_view(), name='job-detail'),
    path('<int:pk>/comments/', views.JobCommentsView.as_view(), name='job-comments'),
    path('<int:pk>/comments/<int:comment_pk>/', views.JobCommentDetailView.as_view(), name='job-comment-detail'),
]
