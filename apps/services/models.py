from django.db import models


class Service(models.Model):
    title = models.CharField(max_length=255)
    short_description = models.TextField()
    long_description = models.TextField()
    image = models.ImageField(upload_to="services/")
    features = models.JSONField(default=list, blank=True)  # list of features
    appreciation_mark = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.title}"
