from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import serializers
from apps.core.models import ClientProfile
from .models import SolarReport, SiteData, Site


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
        # Case-insensitive: usernames must be unique regardless of case, since
        # login is also case-insensitive (so "urban" and "Urban" can't coexist).
        if User.objects.filter(username__iexact=value).exists():
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


class SiteSerializer(serializers.ModelSerializer):
    """Reusable per-client site (identity: name + battery). Admin-managed."""
    client_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='client',
        write_only=True,
    )
    client = ClientUserSerializer(read_only=True)
    data_row_count = serializers.SerializerMethodField()

    class Meta:
        model = Site
        fields = [
            'id', 'client', 'client_id', 'name', 'has_battery',
            'is_active', 'order', 'data_row_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_data_row_count(self, obj):
        # How many report rows reference this site (used to guard hard-delete).
        return obj.data_rows.count()

    def validate(self, attrs):
        # Enforce unique (client, name) with a friendly message (case-insensitive),
        # accounting for create vs. update.
        client = attrs.get('client') or getattr(self.instance, 'client', None)
        name = attrs.get('name') or getattr(self.instance, 'name', None)
        if client and name:
            qs = Site.objects.filter(client=client, name__iexact=name)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {'name': 'This client already has a site with that name.'}
                )
        return attrs


class NestedSiteSerializer(serializers.ModelSerializer):
    """Read-only site identity nested inside a SiteData row."""
    class Meta:
        model = Site
        fields = ['id', 'name', 'has_battery', 'is_active', 'order']


class SiteDataSerializer(serializers.ModelSerializer):
    # NEW: link to a reusable Site by id (write). Optional during the transition —
    # old payloads that send only site_name/has_battery still work.
    site_id = serializers.PrimaryKeyRelatedField(
        queryset=Site.objects.all(),
        source='site',
        write_only=True,
        required=False,
        allow_null=True,
    )
    # Read-only nested identity so the frontend gets the Site's name/battery/active.
    site = NestedSiteSerializer(read_only=True)

    class Meta:
        model = SiteData
        fields = [
            'id', 'order', 'site_id', 'site', 'site_name', 'has_battery',
            'solar_yield', 'battery_charge', 'usable_solar',
            'estimated_saving', 'used_from_battery',
            'sell_to_grid_kwh', 'sell_to_grid_r',
            'grid_consumption', 'total_consumption',
        ]
        # site_name is no longer required on input: when a site_id is given we copy
        # the name/battery from the Site (see SolarReportSerializer._prep_site_row).
        extra_kwargs = {
            'site_name': {'required': False},
            'has_battery': {'required': False},
        }


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
    sibling_reports = serializers.SerializerMethodField()

    class Meta:
        model = SolarReport
        fields = [
            'uuid', 'client', 'client_id', 'report_date',
            'period_start', 'period_end',
            'created_at', 'updated_at', 'sites', 'sibling_reports',
        ]
        read_only_fields = ['uuid', 'created_at', 'updated_at']

    def get_sibling_reports(self, obj):
        if obj.client_id is None:
            return []
        qs = SolarReport.objects.filter(client_id=obj.client_id).order_by('-period_start').values(
            'uuid', 'period_start', 'period_end'
        )
        return [
            {'uuid': str(r['uuid']), 'period_start': r['period_start'], 'period_end': r['period_end']}
            for r in qs
        ]

    @staticmethod
    def _prep_site_row(row, report_client):
        """Normalise one SiteData payload dict before persisting.

        - If a Site was given (`site`), keep the legacy site_name/has_battery columns
          in sync with it (so both old and new readers stay consistent during the
          transition), and verify the Site belongs to the report's client.
        - If no Site was given, require a site_name (old-style payload).
        """
        site = row.get('site')
        if site is not None:
            if report_client is not None and site.client_id != report_client.id:
                raise serializers.ValidationError(
                    {'sites': f"Site '{site.name}' does not belong to this report's client."}
                )
            # Snapshot identity onto the legacy columns.
            row['site_name'] = site.name
            row['has_battery'] = site.has_battery
        else:
            if not row.get('site_name'):
                raise serializers.ValidationError(
                    {'sites': 'Each site row needs either a site_id or a site_name.'}
                )
        return row

    @transaction.atomic
    def create(self, validated_data):
        sites_data = validated_data.pop('sites')
        client = validated_data.get('client')
        # Validate all rows BEFORE any write, so a bad row can't leave a partial report.
        for row in sites_data:
            self._prep_site_row(row, client)
        report = SolarReport.objects.create(**validated_data)
        for row in sites_data:
            SiteData.objects.create(report=report, **row)
        return report

    @transaction.atomic
    def update(self, instance, validated_data):
        sites_data = validated_data.pop('sites', None)
        if sites_data is not None:
            # Validate before mutating anything.
            for row in sites_data:
                self._prep_site_row(row, validated_data.get('client', instance.client))

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if sites_data is not None:
            instance.sites.all().delete()
            for row in sites_data:
                SiteData.objects.create(report=instance, **row)

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
