from django.db import models


class ImportedFile(models.Model):
    """
    A WhatsApp chat export file we have already pulled from the shared Google
    Drive folder and parsed. We key on the Drive file ID so the nightly task can
    list the folder and skip anything already processed — this is what makes the
    import idempotent across nightly runs.
    """
    drive_file_id = models.CharField(max_length=255, unique=True, db_index=True)
    name = models.CharField(max_length=512, blank=True, default='')
    # Drive's own modifiedTime (RFC3339 string) at import — lets us notice if a
    # file was re-uploaded/edited later, without re-parsing on every run.
    drive_modified_time = models.CharField(max_length=64, blank=True, default='')
    message_count = models.PositiveIntegerField(default=0)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-imported_at']

    def __str__(self):
        return f"{self.name or self.drive_file_id} ({self.message_count} msgs)"


class WhatsAppMessage(models.Model):
    """
    A single WhatsApp message parsed out of an exported chat file — one row per
    message. The operations manager reviews these on a web page and flags the
    ones that represent jobs; a separate step then pushes newly-flagged messages
    into the jobs spreadsheet.

    Timestamps: WhatsApp exports have no timezone marker, but the team exports in
    Cape Town (SAST). `sent_at` is stored as a SAST-aware datetime so it displays
    back as the exact wall-clock time that was in the export.
    """
    # --- The message itself ---
    sender = models.CharField(max_length=255, db_index=True)
    sent_at = models.DateTimeField(db_index=True)
    text = models.TextField(blank=True, default='')

    # --- Provenance (for display + tracing a row back to its export) ---
    chat_name = models.CharField(max_length=512, blank=True, default='')
    source_file = models.ForeignKey(
        ImportedFile,
        on_delete=models.CASCADE,
        related_name='messages',
        null=True,
        blank=True,
    )
    imported_at = models.DateTimeField(auto_now_add=True)

    # --- Jobs workflow (set by the ops manager on the review page) ---
    marked_as_job = models.BooleanField(default=False, db_index=True)
    marked_as_job_at = models.DateTimeField(null=True, blank=True)
    # Set once the flagged message has been pushed to the jobs spreadsheet, so
    # the export step only picks up newly-flagged, not-yet-exported messages.
    exported_to_jobs_sheet = models.BooleanField(default=False, db_index=True)
    exported_to_jobs_sheet_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-sent_at']
        indexes = [
            # The review page lists newest-first; the jobs export filters on the
            # two flags. Composite index covers "flagged but not yet exported".
            models.Index(fields=['marked_as_job', 'exported_to_jobs_sheet']),
        ]
        constraints = [
            # Guard against importing the exact same message twice (same sender,
            # timestamp and text from a re-parsed file). Belt to the file-level
            # dedup's braces.
            models.UniqueConstraint(
                fields=['sender', 'sent_at', 'text'],
                name='uniq_whatsapp_message',
            ),
        ]

    def __str__(self):
        preview = (self.text[:40] + '…') if len(self.text) > 40 else self.text
        return f"{self.sender} @ {self.sent_at:%Y-%m-%d %H:%M}: {preview}"
