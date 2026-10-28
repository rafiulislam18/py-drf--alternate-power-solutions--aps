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

    def restore_blocked_reason(self):
        if self.service.in_trash:
            return f'its service “{self.service.title}” is in the trash. Restore the service first.'
        return ''
