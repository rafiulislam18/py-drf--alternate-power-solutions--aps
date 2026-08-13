from django.db import models


class DemoBooking(models.Model):
    """
    A "book a demo" enquiry submitted from the public marketing landing page.

    This is a marketing lead — it has nothing to do with authenticated users
    or scale devices, so it lives in its own app. Submissions are public
    (no login), and each one triggers an email alert to the sales inbox.
    """

    name = models.CharField(max_length=120)
    email = models.EmailField()
    company = models.CharField(max_length=160, blank=True)
    # Free text — the form invites "e.g. 12" but people write "a dozen or so".
    sites = models.CharField('sites to monitor', max_length=120, blank=True)
    message = models.TextField(blank=True)

    # Sales workflow: flip once someone has followed up.
    handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.company or self.name
        return f'{who} <{self.email}>'
