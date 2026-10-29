from django.db import models, transaction

from apps.records.models import TrashableModel


class Service(TrashableModel):
    """A service on the public site. Moving it to the trash takes its live
    projects with it, and restoring it brings those same projects back."""

    title = models.CharField(max_length=255)
    short_description = models.TextField()
    long_description = models.TextField()
    image = models.ImageField(upload_to="services/")
    features = models.JSONField(default=list, blank=True)  # list of features
    appreciation_mark = models.IntegerField(default=0)

    trash_section = 'services'
    trash_label = 'Service'
    trash_file_fields = ('image',)

    def __str__(self):
        return f"{self.title}"

    @transaction.atomic
    def trash(self, user):
        if self.in_trash:
            return
        super().trash(user)
        for project in self.projects.all():  # live ones only
            project.trash(user, at=self.deleted_at, summary=f'With its service “{self.title}”')

    @transaction.atomic
    def restore(self, user):
        if not self.in_trash:
            return
        went_with = list(Project.all_objects.filter(service=self, deleted_at=self.deleted_at))
        super().restore(user)
        for project in went_with:
            project.restore(user, summary=f'With its service “{self.title}”')

    def trash_preview(self, request):
        """The Trash preview: the service as on the website, and the projects that went with it."""
        went_with = Project.all_objects.filter(service=self, deleted_at=self.deleted_at).order_by('title')
        return {
            'kind': 'service',
            'title': self.title, 'short_description': self.short_description,
            'long_description': self.long_description, 'features': self.features or [],
            'image': request.build_absolute_uri(self.image.url) if self.image else None,
            'priority': self.appreciation_mark,
            'projects': [{'title': p.title, 'location': p.location,
                          'image': request.build_absolute_uri(p.image.url) if p.image else None} for p in went_with],
        }

    def stored_files(self):
        # Deleting the service for good deletes its (trashed) projects too.
        files = super().stored_files()
        for project in Project.all_objects.filter(service=self):
            files += project.stored_files()
        return files


class Project(TrashableModel):
    service = models.ForeignKey(
        Service, on_delete=models.CASCADE, related_name="projects"
    )
    title = models.CharField(max_length=255)
    short_description = models.TextField()
    long_description = models.TextField()
    image = models.ImageField(upload_to="projects/")
    image_2 = models.ImageField(upload_to="projects/", null=True, blank=True)
    image_3 = models.ImageField(upload_to="projects/", null=True, blank=True)
    location = models.CharField(max_length=255)
    completion_date = models.DateField()
    duration = models.CharField(max_length=50)  # e.g. "3 months"
    features = models.JSONField(default=list, blank=True)
    appreciation_mark = models.IntegerField(default=0)

    trash_section = 'projects'
    trash_label = 'Project'
    trash_file_fields = ('image', 'image_2', 'image_3')

    def __str__(self):
        return f"{self.title} ({self.service.title})"

    def trash_preview(self, request):
        """The Trash preview: the project as on the website (its gallery, details and service)."""
        return {
            'kind': 'project',
            'title': self.title, 'service': self.service.title, 'service_in_trash': self.service.in_trash,
            'location': self.location, 'completion_date': self.completion_date, 'duration': self.duration,
            'short_description': self.short_description, 'long_description': self.long_description,
            'features': self.features or [],
            'images': [request.build_absolute_uri(getattr(self, f).url)
                       for f in ('image', 'image_2', 'image_3') if getattr(self, f)],
        }

    def restore_blocked_reason(self):
        if self.service.in_trash:
            return f'its service “{self.service.title}” is in the trash. Restore the service first.'
        return ''
