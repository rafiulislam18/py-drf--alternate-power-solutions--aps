from django.db import models


class BlogCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    appreciation_mark = models.IntegerField(default=0)

    class Meta:
        verbose_name_plural = "Blog Categories"

    def __str__(self):
        return f"{self.name}"


class Blog(models.Model):
    category = models.ForeignKey(BlogCategory, on_delete=models.CASCADE, related_name="blogs")
    title = models.CharField(max_length=255)
    short_description = models.TextField()
    long_description = models.TextField()
    image = models.ImageField(upload_to="blogs/")  # since you're storing external image URLs
    author = models.CharField(max_length=100)
    date = models.DateField()
    read_time = models.CharField(max_length=50)  # e.g. "4 min read"
    appreciation_mark = models.IntegerField(default=0)
    
    def __str__(self):
        return f"{self.title}"
