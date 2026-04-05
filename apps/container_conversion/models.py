from django.db import models


class ServiceRequest(models.Model):

    # ---------- Contact Information ----------
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)

    email = models.EmailField()
    phone = models.CharField(max_length=30)

    PREFERRED_CONTACT_CHOICES = [
        ("call", "Phone Call"),
        ("email", "Email"),
        ("whatsapp", "WhatsApp"),
    ]

    preferred_contact_method = models.CharField(
        max_length=10,
        choices=PREFERRED_CONTACT_CHOICES,
        default="call"
    )

    company_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional for individual clients"
    )

    # ---------- Unit Configuration ----------
    UNIT_TYPE_CHOICES = [
        ("container", "Container"),
        ("conversion_only", "Conversion Only (Client-Supplied Container)"),
    ]

    unit_type = models.CharField(
        max_length=20,
        choices=UNIT_TYPE_CHOICES
    )

    INTENDED_USE_CHOICES = [
        ("storage", "Storage"),
        ("office", "Office"),
        ("accommodation", "Accommodation"),
        ("classroom_or_school", "Classroom / School"),
        ("site_office", "Site Office"),
        ("ablution", "Ablution"),
    ]

    intended_use = models.CharField(
        max_length=20,
        choices=INTENDED_USE_CHOICES
    )

    MODULAR_SIZE_CHOICES = [
        ("6m", "6 Meter (20ft Container)"),
        ("12m", "12 Meter (40ft Container)"),
        ("client_supplied_container", "Client-Supplied Container"),
    ]

    modular_size = models.CharField(
        max_length=25,
        choices=MODULAR_SIZE_CHOICES
    )

    # ---------- Optional Extras ----------
    ablution_unit = models.BooleanField(
        default=False,
        help_text="Include toilet / washroom facilities"
    )
    electrical_installation = models.BooleanField(
        default=False,
        help_text="Internal wiring, plugs, lights, and DB board installation"
    )
    plumbing_installation = models.BooleanField(
        default=False,
        help_text="Water supply and drainage points where required"
    )
    insulation = models.BooleanField(
        default=False,
        help_text="Thermal insulation for improved temperature control"
    )
    interior_finishes = models.BooleanField(
        default=False,
        help_text="Internal cladding, ceilings, flooring, and finish upgrades"
    )
    air_conditioning = models.BooleanField(
        default=False,
        help_text="Supply and installation of air conditioning"
    )
    solar_backup_power = models.BooleanField(
        default=False,
        help_text="Solar, inverter, and battery backup solutions"
    )
    custom_painting_branding = models.BooleanField(
        default=False,
        help_text="Custom exterior paint colours or branded finishes"
    )
    delivery_and_installation = models.BooleanField(
        default=False,
        help_text="Transport, offloading, positioning, and installation on site"
    )

    # ---------- Project Details ----------
    PROJECT_TIMEFRAME_CHOICES = [
        ("urgent", "Urgent"),
        ("within_1_month", "Within 1 Month"),
        ("1_to_3_months", "1–3 Months"),
        ("budgeting_only", "Budgeting Only"),
    ]

    project_timeframe = models.CharField(
        max_length=20,
        choices=PROJECT_TIMEFRAME_CHOICES,
        blank=True,
        help_text="Client's project timeline"
    )

    BUDGET_RANGE_CHOICES = [
        ("100_to_250k", "R100,000 – R250,000"),
        ("250_to_500k", "R250,000 – R500,000"),
        ("500k_to_1m", "R500,000 – R1,000,000"),
        ("over_1m", "Over R1,000,000"),
        ("not_sure", "Not Sure Yet"),
    ]

    budget_range = models.CharField(
        max_length=20,
        choices=BUDGET_RANGE_CHOICES,
        default="not_sure",
        help_text="Client's budget range"
    )

    FINISH_LEVEL_CHOICES = [
        ("standard", "Standard"),
        ("mid_range", "Mid-Range"),
        ("premium", "Premium"),
        ("to_be_discussed", "To Be Discussed"),
    ]

    finish_level = models.CharField(
        max_length=20,
        choices=FINISH_LEVEL_CHOICES,
        default="standard",
        help_text="Desired finish level / fittings preference"
    )

    # ---------- Delivery & Other Details ----------
    transport_or_export_address = models.TextField()
    additional_details = models.TextField(blank=True, help_text="Any additional requirements or special requests")

    # ---------- Admin ----------
    notes = models.TextField(blank=True)
    is_processed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Service Request"
        verbose_name_plural = "Service Requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.modular_size}"
