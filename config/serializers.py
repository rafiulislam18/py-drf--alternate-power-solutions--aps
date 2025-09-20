from rest_framework import serializers
from apps.services_and_projects.models import Service, Project


class HomePageServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'title', 'short_description', 'image']

class HomePageProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['id', 'title', 'short_description', 'image']
