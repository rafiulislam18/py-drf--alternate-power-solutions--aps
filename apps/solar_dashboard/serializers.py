from rest_framework import serializers
from .models import SolarReport, SiteData


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

    class Meta:
        model = SolarReport
        fields = [
            'uuid', 'client_name', 'report_date',
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

    class Meta:
        model = SolarReport
        fields = [
            'uuid', 'client_name', 'report_date',
            'period_start', 'period_end',
            'created_at', 'site_count',
        ]

    def get_site_count(self, obj):
        return obj.sites.count()
