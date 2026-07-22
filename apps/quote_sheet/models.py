from django.db import models


class ExportedQuote(models.Model):
    """
    Tracks which quote requests have been pushed to their Google Sheet tab, so the
    sync can report what's new vs. already-there.

    Three kinds, each going to its own tab in the same "APS Open Jobs" spreadsheet:
      - regular   -> apps.quote_request.QuoteRequest (non-solar) -> "Website Quote Requests"
      - solar     -> apps.quote_request.QuoteRequest where service is
                     "Solar PV System Design & Installation"      -> "Website Solar Quote Requests"
      - container -> apps.container_conversion.ServiceRequest     -> "Website Container Conversion Quotes"

    regular and solar both come from QuoteRequest and share the id space, but a
    quote is classified as exactly one of them (by service title), so its dedup key
    is either "regular:<id>" OR "solar:<id>", never both. The source tables share PK
    ranges across apps, hence the composite (kind, quote_id) key. The sheet upserts
    by the same key, refreshing columns on existing rows while leaving any
    admin-added Notes column untouched.
    """
    KIND_REGULAR = 'regular'      # apps.quote_request (non-solar) -> Website Quote Requests
    KIND_SOLAR = 'solar'          # apps.quote_request (solar PV)  -> Website Solar Quote Requests
    KIND_CONTAINER = 'container'  # apps.container_conversion       -> Website Container Conversion Quotes
    KIND_CHOICES = (
        (KIND_REGULAR, 'Website Quote Requests'),
        (KIND_SOLAR, 'Website Solar Quote Requests'),
        (KIND_CONTAINER, 'Website Container Conversion Quotes'),
    )

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    quote_id = models.PositiveIntegerField()
    first_exported_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'quote_id'],
                name='uniq_exported_quote',
            ),
        ]

    @property
    def key(self):
        return f"{self.kind}:{self.quote_id}"

    def __str__(self):
        return self.key
