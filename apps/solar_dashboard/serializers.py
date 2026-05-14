from django.contrib.auth.models import User
from rest_framework import serializers
from apps.core.models import ClientProfile
from .models import SolarReport, SiteData


class ClientProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientProfile
        fields = ['role', 'company_name', 'image']


class ClientUserSerializer(serializers.ModelSerializer):
    """Read-only minimal client representation for dropdowns and report display."""
    company_name = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'company_name', 'image', 'role']

    def _profile(self, obj):
        try:
            return obj.client_profile
        except Exception:
            return None

    def get_company_name(self, obj):
        p = self._profile(obj)
        return p.company_name if p else ''

    def get_image(self, obj):
        p = self._profile(obj)
        if not p or not p.image:
            return None
        return p.image.url

    def get_role(self, obj):
        p = self._profile(obj)
        return p.role if p else 'client'


class CreateClientUserSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    company_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    image = serializers.ImageField(required=False, allow_null=True)
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('A user with that username already exists.')
        return value

    def validate(self, data):
        if data['password'] != data.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return data

    def create(self, validated_data):
        company_name = validated_data.pop('company_name', '')
        image = validated_data.pop('image', None)
        password = validated_data.pop('password')
        user = User.objects.create_user(username=validated_data['username'], password=password)
        ClientProfile.objects.create(user=user, role='client', company_name=company_name, image=image)
        return user


class SiteDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteData
        fields = [
            'id', 'order', 'site_name', 'has_battery',
            'solar_yield', 'battery_charge', 'usable_solar',
            'estimated_saving', 'used_from_battery',
            'sell_to_grid_kwh', 'sell_to_grid_r',
            'grid_consumption', 'total_consumption',
        ]


class SolarReportSerializer(serializers.ModelSerializer):
    sites = SiteDataSerializer(many=True)
    client = ClientUserSerializer(read_only=True)
    client_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='client',
        write_only=True,
        allow_null=True,
        required=False,
    )

    class Meta:
        model = SolarReport
        fields = [
            'uuid', 'client', 'client_id', 'report_date',
            'period_start', 'period_end',
            'created_at', 'updated_at', 'sites',
        ]
        read_only_fields = ['uuid', 'created_at', 'updated_at']

    def create(self, validated_data):
        sites_data = validated_data.pop('sites')
        report = SolarReport.objects.create(**validated_data)
        for site in sites_data:
            SiteData.objects.create(report=report, **site)
        return report

    def update(self, instance, validated_data):
        sites_data = validated_data.pop('sites', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if sites_data is not None:
            instance.sites.all().delete()
            for site in sites_data:
                SiteData.objects.create(report=instance, **site)

        return instance


class SolarReportListSerializer(serializers.ModelSerializer):
    site_count = serializers.SerializerMethodField()
    client = ClientUserSerializer(read_only=True)

    class Meta:
        model = SolarReport
        fields = [
            'uuid', 'client', 'report_date',
            'period_start', 'period_end',
            'created_at', 'site_count',
        ]

    def get_site_count(self, obj):
        return obj.sites.count()
