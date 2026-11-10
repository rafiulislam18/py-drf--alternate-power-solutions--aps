from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import serializers

from apps.solar_dashboard.models import Site

from .models import Job, JobCategory, JobSettings, staff_name


def staff_queryset():
    """Active staff accounts — the people a job can be assigned to."""
    from apps.staff.views import staff_users
    return staff_users().filter(is_active=True)


def client_queryset():
    return User.objects.filter(client_profile__role='client', is_active=True)


def client_label(user):
    profile = getattr(user, 'client_profile', None)
    return (profile.company_name if profile else '') or user.get_full_name() or user.username


def person(user):
    return {'id': user.pk, 'name': staff_name(user)} if user else None


def age_and_severity(job, thresholds, today=None):
    """(days since the last update, 0 fine / 1 watch / 2 aging / 3 critical).
    Only live jobs (open, quote sent, in progress) get a severity."""
    today = today or timezone.localdate()
    age = (today - timezone.localtime(job.last_updated).date()).days
    if job.status not in Job.LIVE:
        return age, 0
    if age >= thresholds.critical_days:
        return age, 3
    if age >= thresholds.aging_days:
        return age, 2
    if age >= thresholds.watch_days:
        return age, 1
    return age, 0


def job_row(job, thresholds, today=None):
    """A job in the list."""
    age, severity = age_and_severity(job, thresholds, today)
    comments = list(job.comments.all())  # prefetched
    last = comments[-1] if comments else None
    return {
        'id': job.pk,
        'job_id': job.job_id,
        'number': job.number,
        'category': job.category_id,
        'category_label': job.category.name,
        'client_name': job.client_name,
        'task': job.task,
        'assigned_to': [person(u) for u in job.assigned_to.all()],
        'status': job.status,
        'status_label': job.get_status_display(),
        'payment': job.payment,
        'payment_label': job.get_payment_display() if job.payment else '',
        'date_added': job.date_added,
        'last_updated': job.last_updated,
        'age_days': age,
        'severity': severity,
        'comment_count': len(comments),
        'last_comment': {'author': last.author_name, 'body': last.body, 'created_at': last.created_at} if last else None,
    }


def comment_data(comment, user):
    return {
        'id': comment.pk,
        'author': {'id': comment.author_id, 'name': comment.author_name} if comment.author_name else None,
        'body': comment.body,
        'created_at': comment.created_at,
        'edited_at': comment.edited_at,
        'mine': bool(user and comment.author_id == user.pk),
    }


def job_detail(job, thresholds, user):
    """One job with every field and its comments (oldest first)."""
    return {
        **job_row(job, thresholds),
        'client': {'id': job.client_id, 'name': client_label(job.client)} if job.client_id else None,
        'site': {'id': job.site_id, 'name': job.site.name} if job.site_id else None,
        'call_date': job.call_date,
        'assessment': job.assessment,
        'follow_up_date': job.follow_up_date,
        'scheduled_date': job.scheduled_date,
        'scheduled_time': job.scheduled_time,
        'coc_issued': job.coc_issued,
        'invoice': job.invoice,
        'completion_date': job.completion_date,
        'created_by': staff_name(job.created_by) if job.created_by_id else '',
        'comments': [comment_data(c, user) for c in job.comments.all()],
    }


class JobWriteSerializer(serializers.ModelSerializer):
    """What staff fill in on the job form (create and edit)."""

    assigned_to = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=User.objects.none(),
        error_messages={'does_not_exist': 'That staff member no longer exists or is switched off.'},
    )
    client = serializers.PrimaryKeyRelatedField(
        required=False, allow_null=True, queryset=User.objects.none(),
        error_messages={'does_not_exist': 'That client account no longer exists.'},
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=JobCategory.objects.all(),
        error_messages={'does_not_exist': 'That category no longer exists.', 'required': 'Choose a category.',
                        'null': 'Choose a category.'},
    )
    site = serializers.PrimaryKeyRelatedField(
        required=False, allow_null=True, queryset=Site.objects.all(),
        error_messages={'does_not_exist': 'That site no longer exists.'},
    )
    # A comment written while creating the job (the first entry of its thread).
    comment = serializers.CharField(required=False, allow_blank=True, write_only=True, max_length=5000)

    class Meta:
        model = Job
        fields = [
            'category', 'client_name', 'client', 'site', 'task', 'call_date', 'assessment', 'follow_up_date',
            'scheduled_date', 'scheduled_time', 'coc_issued', 'invoice', 'assigned_to', 'status', 'payment',
            'completion_date', 'comment',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].child_relation.queryset = staff_queryset()
        self.fields['client'].queryset = client_queryset()

    def validate_task(self, value):
        return value.strip()

    def validate_category(self, value):
        # A switched-off category can stay on the jobs that already use it.
        if not value.is_active and getattr(self.instance, 'category_id', None) != value.pk:
            raise serializers.ValidationError('That category is switched off. Pick another one.')
        return value

    def validate(self, attrs):
        current = self.instance
        get = lambda name: attrs[name] if name in attrs else getattr(current, name, None)  # noqa: E731
        if not (get('client_name') or '').strip() and not (get('task') or '').strip():
            raise serializers.ValidationError({'client_name': 'Enter the client / job or a task.'})

        client, site = get('client'), get('site')
        if site is not None:
            if 'client' in attrs and client is None and 'site' not in attrs:
                attrs['site'] = None  # the client link was removed, so its site goes too
            elif client is None:
                attrs['client'] = site.client  # picking a site links its client
            elif site.client_id != client.pk:
                raise serializers.ValidationError({'site': 'That site belongs to a different client.'})
        return attrs


class JobCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = JobCategory
        fields = ['id', 'name', 'order', 'is_active']
        # validate_name checks uniqueness itself, ignoring case.
        extra_kwargs = {'order': {'required': False}, 'name': {'validators': []}}

    def validate_name(self, value):
        name = ' '.join(value.split())
        clash = JobCategory.objects.filter(name__iexact=name)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError('There is already a category with this name.')
        return name


class CommentSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=5000, trim_whitespace=True)


class JobSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobSettings
        fields = ['watch_days', 'aging_days', 'critical_days']
        extra_kwargs = {f: {'min_value': 1, 'max_value': 365} for f in fields}

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name))  # noqa: E731
        if not get('watch_days') < get('aging_days') < get('critical_days'):
            raise serializers.ValidationError(
                {'aging_days': 'Each step needs more days than the one before (watch < aging < critical).'})
        return attrs
