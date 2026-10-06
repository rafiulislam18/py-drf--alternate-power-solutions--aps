from rest_framework import serializers
from apps.core.content_files import validate_image_size
from .models import *


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = '__all__'


class ServiceSerializer(serializers.ModelSerializer):
    projects = ProjectSerializer(many=True)

    class Meta:
        model = Service
        fields = '__all__'


class ServiceListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'title']


# ---------------------------------------------------------------------------
# Dashboard "Site content" editor (admin only). The public serializers above
# stay untouched; these add write support and a few list-view extras.
# ---------------------------------------------------------------------------

def _clean_features(value):
    if not isinstance(value, list) or not all(isinstance(f, str) for f in value):
        raise serializers.ValidationError('Features must be a list of text items.')
    return [f.strip() for f in value if f.strip()]


class AdminServiceSerializer(serializers.ModelSerializer):
    project_count = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = [
            'id', 'title', 'short_description', 'long_description', 'image',
            'features', 'appreciation_mark', 'project_count',
        ]

    def get_project_count(self, obj):
        annotated = getattr(obj, 'num_projects', None)
        return annotated if annotated is not None else obj.projects.count()

    def validate_features(self, value):
        return _clean_features(value)

    def validate_image(self, value):
        return validate_image_size(value)


class AdminProjectSerializer(serializers.ModelSerializer):
    service_title = serializers.CharField(source='service.title', read_only=True)
    # The optional gallery images can be cleared from the editor; the main
    # image is required, so it can only be replaced.
    remove_image_2 = serializers.BooleanField(write_only=True, required=False)
    remove_image_3 = serializers.BooleanField(write_only=True, required=False)

    class Meta:
        model = Project
        fields = [
            'id', 'service', 'service_title', 'title', 'short_description',
            'long_description', 'image', 'image_2', 'image_3', 'location',
            'completion_date', 'duration', 'features', 'appreciation_mark',
            'remove_image_2', 'remove_image_3',
        ]

    def validate_features(self, value):
        return _clean_features(value)

    def validate_image(self, value):
        return validate_image_size(value)

    def validate_image_2(self, value):
        return validate_image_size(value)

    def validate_image_3(self, value):
        return validate_image_size(value)

    def _apply_removals(self, validated_data):
        for field in ('image_2', 'image_3'):
            if validated_data.pop(f'remove_{field}', False) and not validated_data.get(field):
                validated_data[field] = None
        return validated_data

    def create(self, validated_data):
        return super().create(self._apply_removals(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._apply_removals(validated_data))
