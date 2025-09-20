from django.contrib import admin
from .models import Blog, BlogCategory


class BlogInline(admin.TabularInline):
    model = Blog
    extra = 0
    fields = ('title', 'author', 'short_description', 'date')


@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'appreciation_mark')
    search_fields = ('name',)
    ordering = ('-appreciation_mark', 'name')
    inlines = [BlogInline]


@admin.register(Blog)
class BlogAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'category', 'author', 'date', 'read_time', 'appreciation_mark')
    list_filter = ('category', 'date')
    search_fields = ('title', 'category__name', 'short_description', 'long_description', 'author')
    ordering = ('-appreciation_mark', 'title')
