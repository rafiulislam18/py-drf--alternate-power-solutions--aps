from django.db import models
from apps.services.models import Service


class Project(models.Model):
    service = models.ForeignKey(
        Service, on_delete=models.CASCADE, related_name="projects"
    )
    title = models.CharField(max_length=255)
    short_description = models.TextField()
    long_description = models.TextField()
    image = models.ImageField(upload_to="projects/")
    location = models.CharField(max_length=255)
    completion_date = models.DateField()
    duration = models.CharField(max_length=50)  # e.g. "3 months"
    features = models.JSONField(default=list, blank=True)
    appreciation_mark = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.title} ({self.service.title})"
