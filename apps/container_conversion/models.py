from django.db import models


class ServiceRequest(models.Model):

    # ---------- CORE BUSINESS INFO ----------
    company_name = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ---------- MODULAR / CONTAINER TYPE ----------
    MODULAR_SIZE_CHOICES = [
        ("3m", "3 Meter"),
        ("6m", "6 Meter (20ft Container)"),
        ("12m", "12 Meter (40ft Container)"),
        ("9m_parkhome", "9 Meter Parkhome"),
        ("conversion_only", "Conversion Only (Client-Owned Container)"),
    ]

    modular_size = models.CharField(
        max_length=20,
        choices=MODULAR_SIZE_CHOICES,
        help_text="Primary container or modular unit size"
    )

    # ---------- DOMESHELTER ----------
    DOMESHELTER_CHOICES = [
        ("none", "No Dome Shelter"),
        ("6x6", "6m x 6m"),
        ("12x12", "12m x 12m"),
        ("18x18", "18m x 18m"),
    ]

    domeshelter_size = models.CharField(
        max_length=10,
        choices=DOMESHELTER_CHOICES,
        default="none",
        blank=True
    )

    # ---------- RENT OR BUY ----------
    RENT_BUY_CHOICES = [
        ("rent", "Rent"),
        ("buy", "Buy"),
    ]

    rent_or_buy = models.CharField(
        max_length=10,
        choices=RENT_BUY_CHOICES
    )

    # ---------- OPTIONAL ADD-ONS ----------
    rent_furniture = models.BooleanField(default=False)
    flatpack = models.BooleanField(
        default=False,
        help_text="Delivered unassembled and assembled on site"
    )

    # ---------- REFRIGERATED CONTAINER ----------
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

    # ---------- TRANSPORT / EXPORT ----------
    transport_or_export_address = models.TextField(
        help_text="Delivery address or export destination"
    )

    # ---------- ADMIN / SALES ----------
    is_processed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "Service Requests"

    def __str__(self):
        return f"{self.company_name} - {self.modular_size} ({self.rent_or_buy})"
