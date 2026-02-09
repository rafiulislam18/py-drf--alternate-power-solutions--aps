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
        ("parkhome", "Parkhome"),
    ]

    unit_type = models.CharField(
        max_length=10,
        choices=UNIT_TYPE_CHOICES
    )

    INTENDED_USE_CHOICES = [
        ("storage", "Storage"),
        ("office", "Office"),
        ("accommodation", "Accommodation"),
        ("school", "School"),
        ("custom_conversion", "Custom Conversion"),
    ]

    intended_use = models.CharField(
        max_length=20,
        choices=INTENDED_USE_CHOICES
    )

    ablution_included = models.BooleanField(
        default=False,
        help_text="Included toilet / washroom unit"
    )

    MODULAR_SIZE_CHOICES = [
        ("3m", "3 Meter"),
        ("6m", "6 Meter (20ft Container)"),
        ("12m", "12 Meter (40ft Container)"),
        ("9m_parkhome", "9 Meter Parkhome"),
        ("conversion_only", "Conversion Only (Client-Owned Container)"),
    ]

    modular_size = models.CharField(
        max_length=20,
        choices=MODULAR_SIZE_CHOICES
    )

    DOMESHELTER_CHOICES = [
        ("none", "No Dome Shelter"),
        ("6x6", "6m x 6m"),
        ("12x12", "12m x 12m"),
        ("18x18", "18m x 18m"),
    ]

    domeshelter_size = models.CharField(
        max_length=10,
        choices=DOMESHELTER_CHOICES,
        default="none"
    )

    RENT_BUY_CHOICES = [
        ("rent", "Rent"),
        ("buy", "Buy"),
    ]

    rent_or_buy = models.CharField(
        max_length=10,
        choices=RENT_BUY_CHOICES
    )

    flatpack = models.BooleanField(default=False, help_text="Delivered unassembled & assembled on-site")
    rent_furniture = models.BooleanField(default=False)

    REFRIGERATION_CHOICES = [
        ("none", "Not Required"),
        ("single_phase", "Single-Phase"),
        ("three_phase", "Three-Phase"),
    ]

    refrigeration_type = models.CharField(
        max_length=20,
        choices=REFRIGERATION_CHOICES,
        default="none"
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
