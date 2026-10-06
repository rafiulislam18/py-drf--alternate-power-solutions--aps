from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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


class PublicClientSerializer(ClientUserSerializer):
    """Client identity as shown on a report — which is public via its share
    link, so it leaves out the login username (and role)."""

    class Meta(ClientUserSerializer.Meta):
        fields = ['id', 'company_name', 'image']


MAX_LOGO_BYTES = 5 * 1024 * 1024


class CreateClientUserSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    company_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    # Optional. Stored on the login user and accepted instead of the username
    # at sign-in, so it must identify exactly one account.
    email = serializers.EmailField(max_length=254, required=False, allow_blank=True)
    image = serializers.ImageField(required=False, allow_null=True)
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_username(self, value):
        value = value.strip()
        # Same character rules as Django's own username field.
        try:
            User._meta.get_field('username').run_validators(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        # Case-insensitive: usernames must be unique regardless of case, since
        # login is also case-insensitive (so "urban" and "Urban" can't coexist).
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('A user with that username already exists.')
        # Sign-in accepts a username or an email, so a username may not be
        # another account's email either.
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('That username is already used as another account’s email.')
        return value

    def validate_email(self, value):
        value = User.objects.normalize_email(value.strip())
        if not value:
            return ''
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with that email already exists.')
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('That email is already used as another account’s username.')
        return value

    def validate_image(self, value):
        if value and value.size > MAX_LOGO_BYTES:
            raise serializers.ValidationError('The logo must be 5 MB or smaller.')
        return value

    def validate(self, data):
        if data['password'] != data.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        # Django's password validators (min length 8, not too common, ...).
        try:
            validate_password(data['password'],
                              user=User(username=data.get('username', ''), email=data.get('email', '')))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})
        return data

    @transaction.atomic
    def create(self, validated_data):
        # Atomic: a User without its ClientProfile would be treated as an admin
        # (see get_role), so never leave one behind if the profile save fails.
        company_name = validated_data.pop('company_name', '')
        image = validated_data.pop('image', None)
        password = validated_data.pop('password')
        email = validated_data.pop('email', '')
        user = User.objects.create_user(username=validated_data['username'], email=email, password=password)
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
            'id', 'client', 'client_id', 'name', 'address', 'has_battery',
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
    # Link to a reusable Site by id (write). Required now that identity lives on Site.
    site_id = serializers.PrimaryKeyRelatedField(
        queryset=Site.objects.all(),
        source='site',
        write_only=True,
    )
    # Read-only nested identity so the frontend gets the Site's name/battery/active.
    site = NestedSiteSerializer(read_only=True)

    NUMERIC_FIELDS = [
        'solar_yield', 'battery_charge', 'usable_solar',
        'estimated_saving', 'used_from_battery',
        'sell_to_grid_kwh', 'sell_to_grid_r',
        'grid_consumption', 'total_consumption',
    ]

    class Meta:
        model = SiteData
        fields = [
            'id', 'order', 'site_id', 'site',
            'solar_yield', 'battery_charge', 'usable_solar',
            'estimated_saving', 'used_from_battery',
            'sell_to_grid_kwh', 'sell_to_grid_r',
            'grid_consumption', 'total_consumption',
        ]

    def validate(self, attrs):
        negative = [f for f in self.NUMERIC_FIELDS if attrs.get(f) is not None and attrs[f] < 0]
        if negative:
            raise serializers.ValidationError(
                {f: 'Must be zero or more.' for f in negative}
            )
        return attrs

    def to_representation(self, instance):
        """Expose site_name / has_battery (sourced from the linked Site) for the
        frontend, which reads these convenience keys. Identity of record is the Site.

        `in_report=True` marks a real saved row (vs. a padded all-zero site that the
        report serializer adds for active sites with no data this period).
        """
        data = super().to_representation(instance)
        if instance.site_id and instance.site:
            data['site_name'] = instance.site.name
            data['has_battery'] = instance.site.has_battery
        else:
            data['site_name'] = ''
            data['has_battery'] = False
        data['in_report'] = True
        return data


class SolarReportSerializer(serializers.ModelSerializer):
    sites = SiteDataSerializer(many=True)
    client = PublicClientSerializer(read_only=True)
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

    def validate(self, attrs):
        start = attrs.get('period_start', getattr(self.instance, 'period_start', None))
        end = attrs.get('period_end', getattr(self.instance, 'period_end', None))
        if start and end and start > end:
            raise serializers.ValidationError({'period_end': 'Period end must be on or after the period start.'})

        # Periods may share a boundary day (one ends on the 17th, the next
        # starts on the 17th), so only an exact duplicate period is refused.
        client = attrs.get('client', getattr(self.instance, 'client', None))
        if client is not None and start and end:
            dupes = SolarReport.objects.filter(client=client, period_start=start, period_end=end)
            if self.instance is not None:
                dupes = dupes.exclude(pk=self.instance.pk)
            if dupes.exists():
                raise serializers.ValidationError({
                    'period_start': (
                        'This client already has a report for '
                        f'{start:%d/%m/%Y} – {end:%d/%m/%Y}. Edit that report instead.'
                    ),
                })
        return attrs

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

    def to_representation(self, instance):
        """Pad the read `sites` list to the full fleet for this client.

        The exact-period view should list the same sites as the range views: every
        active site, plus any inactive site that has data in this report. Sites with
        no row in this report appear as all-zero rows with in_report=False; real rows
        keep their data with in_report=True. Padded rows are display-only (the edit
        form ticks the checkbox from in_report, and writes send only real rows).
        """
        data = super().to_representation(instance)
        if instance.client_id is None:
            return data  # unassigned report: just its own rows

        real_site_ids = {row.site_id for row in instance.sites.all() if row.site_id}
        active_sites = Site.objects.filter(client_id=instance.client_id, is_active=True)
        padding = []
        for site in active_sites:
            if site.id in real_site_ids:
                continue  # already present as a real row
            pad = {
                'id': None,
                'order': 0,
                'site': NestedSiteSerializer(site).data,
                'site_name': site.name,
                'has_battery': site.has_battery,
                'in_report': False,
            }
            for f in SiteDataSerializer.NUMERIC_FIELDS:
                pad[f] = '0.00'
            padding.append(pad)

        # Real rows first (keep their order), then padded active sites by name.
        padding.sort(key=lambda p: (p['site']['order'], p['site_name']))
        data['sites'] = list(data['sites']) + padding
        return data

    @staticmethod
    def _prep_site_row(row, report_client):
        """Validate one SiteData payload dict before persisting.

        Each row must reference a Site (site_id) that belongs to the report's client.
        """
        site = row.get('site')
        if site is None:
            raise serializers.ValidationError(
                {'sites': 'Each site row needs a site_id.'}
            )
        if report_client is None:
            # Without a client there's nothing to check the site against, and the
            # public report would show another client's site names.
            raise serializers.ValidationError(
                {'client_id': 'Choose a client before adding sites.'}
            )
        if site.client_id != report_client.id:
            raise serializers.ValidationError(
                {'sites': f"Site '{site.name}' does not belong to this report's client."}
            )
        return row

    @classmethod
    def _validate_rows(cls, sites_data, report_client):
        """Validate every row, and refuse the same site twice in one report
        (the dashboard would show it as two cards and double its totals)."""
        seen = set()
        for row in sites_data:
            cls._prep_site_row(row, report_client)
            site = row['site']
            if site.id in seen:
                raise serializers.ValidationError(
                    {'sites': f"Site '{site.name}' appears more than once in this report."}
                )
            seen.add(site.id)

    @transaction.atomic
    def create(self, validated_data):
        sites_data = validated_data.pop('sites')
        client = validated_data.get('client')
        # Validate all rows BEFORE any write, so a bad row can't leave a partial report.
        self._validate_rows(sites_data, client)
        report = SolarReport.objects.create(**validated_data)
        for row in sites_data:
            SiteData.objects.create(report=report, **row)
        return report

    @transaction.atomic
    def update(self, instance, validated_data):
        sites_data = validated_data.pop('sites', None)
        if sites_data is not None:
            # Validate before mutating anything.
            self._validate_rows(sites_data, validated_data.get('client', instance.client))

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
