from django.db import models
from apps.services_and_projects.models import Service


class QuoteRequest(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    company = models.CharField(max_length=255, blank=True, null=True)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='quote_requests')
    message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_quote = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Quote Requests"

    def __str__(self):
        return f"Quote Request from {self.name} - {self.email}"


class SolarQuoteDetails(models.Model):
    """Extra info collected when the requested service is Solar PV System Design & Installation."""

    PROPERTY_TYPE_CHOICES = [
        ('residential', 'Residential'),
        ('commercial', 'Commercial'),
        ('industrial', 'Industrial'),
        ('agricultural', 'Agricultural'),
    ]

    PROVINCE_CHOICES = [
        ('western_cape', 'Western Cape'),
        ('gauteng', 'Gauteng'),
        ('kwazulu_natal', 'KwaZulu-Natal'),
        ('eastern_cape', 'Eastern Cape'),
        ('limpopo', 'Limpopo'),
        ('mpumalanga', 'Mpumalanga'),
        ('free_state', 'Free State'),
        ('north_west', 'North West'),
        ('northern_cape', 'Northern Cape'),
    ]

    ROOF_TYPE_CHOICES = [
        ('ibr_corrugated', 'IBR / corrugated iron'),
        ('concrete_flat', 'Concrete slab / flat roof'),
        ('tiled', 'Tiled'),
        ('klip_lok', 'Klip-Lok'),
        ('asbestos', 'Asbestos'),
        ('other', 'Other'),
    ]

    PRIMARY_GOAL_CHOICES = [
        ('backup', 'Backup power'),
        ('savings', 'Save on bills'),
        ('offgrid', 'Go off-grid'),
        ('all', 'All of the above'),
    ]

    GRID_CONNECTION_CHOICES = [
        ('single_phase_eskom', 'Single phase (Eskom direct)'),
        ('three_phase_eskom', 'Three phase (Eskom direct)'),
        ('prepaid_meter', 'Prepaid meter'),
        ('municipality_metered', 'Municipality metered'),
        ('no_connection', 'No current connection'),
    ]

    BUDGET_RANGE_CHOICES = [
        ('under50k', 'Under R50k'),
        ('50-150k', 'R50k - R150k'),
        ('150-300k', 'R150k - R300k'),
        ('over300k', 'Over R300k'),
    ]

    TIMELINE_CHOICES = [
        ('asap', 'ASAP - within 1 month'),
        ('1_to_3_months', '1-3 months'),
        ('3_to_6_months', '3-6 months'),
        ('exploring', 'Just exploring options'),
    ]

    REFERRAL_SOURCE_CHOICES = [
        ('referral', 'Referral / word of mouth'),
        ('google', 'Google search'),
        ('social_media', 'Facebook / Instagram'),
        ('existing_client', 'Existing APS client'),
        ('other', 'Other'),
    ]

    quote_request = models.OneToOneField(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name='solar_details',
    )
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPE_CHOICES, blank=True, null=True)
    suburb = models.CharField(max_length=255, blank=True, null=True)
    province = models.CharField(max_length=30, choices=PROVINCE_CHOICES, blank=True, null=True)
    roof_type = models.CharField(max_length=30, choices=ROOF_TYPE_CHOICES, blank=True, null=True)
    monthly_bill = models.PositiveIntegerField(blank=True, null=True)
    primary_goal = models.CharField(max_length=20, choices=PRIMARY_GOAL_CHOICES, blank=True, null=True)
    grid_connection = models.CharField(max_length=30, choices=GRID_CONNECTION_CHOICES, blank=True, null=True)
    budget_range = models.CharField(max_length=20, choices=BUDGET_RANGE_CHOICES, blank=True, null=True)
    timeline = models.CharField(max_length=20, choices=TIMELINE_CHOICES, blank=True, null=True)
    additional_notes = models.TextField(blank=True, null=True)
    referral_source = models.CharField(max_length=20, choices=REFERRAL_SOURCE_CHOICES, blank=True, null=True)

    class Meta:
        verbose_name_plural = "Solar Quote Details"

    def __str__(self):
        return f"Solar details for {self.quote_request.name}"
