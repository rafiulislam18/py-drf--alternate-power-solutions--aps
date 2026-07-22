from django.db import models


class ExportedQuote(models.Model):
    """
    Tracks which quote requests have been pushed to their Google Sheet tab, so the
    sync can report what's new vs. already-there.

    Two kinds, each going to its own tab in the same "APS Open Jobs" spreadsheet:
      - regular   -> apps.quote_request.QuoteRequest      -> "Quote Requests" tab
      - container -> apps.container_conversion.ServiceRequest -> "Container Conversion Quotes" tab

    The two source tables share PK ranges, so we key on (kind, quote_id). The sheet
    upserts by the same composite key ("<kind>:<id>"), refreshing columns on
    existing rows while leaving any admin-added Notes column untouched.
    """
    KIND_REGULAR = 'regular'      # apps.quote_request -> Quote Requests
    KIND_CONTAINER = 'container'  # apps.container_conversion -> Container Conversion Quotes
    KIND_CHOICES = (
        (KIND_REGULAR, 'Quote Requests'),
        (KIND_CONTAINER, 'Container Conversion Quotes'),
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
