import os

from django.utils import timezone
from rest_framework import serializers

from apps.services_and_projects.models import Service
from apps.solar_dashboard.models import Site

from .models import MESSAGE_MAX_LENGTH, Ticket, TicketMessage

# Upload limits for the new-ticket form ("JPG, PNG or PDF, up to 10 files").
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Extension → (stored content type, leading magic bytes). The bytes are checked
# as well as the name so a renamed executable can't ride in as "photo.jpg".
_ALLOWED_TYPES = {
    '.jpg': ('image/jpeg', (b'\xff\xd8\xff',)),
    '.jpeg': ('image/jpeg', (b'\xff\xd8\xff',)),
    '.png': ('image/png', (b'\x89PNG\r\n\x1a\n',)),
    '.pdf': ('application/pdf', (b'%PDF',)),
}


def validate_attachments(files):
    """Check the uploaded files; returns ``[(file, content_type), ...]`` or raises."""
    if len(files) > MAX_ATTACHMENTS:
        raise serializers.ValidationError(f'You can attach up to {MAX_ATTACHMENTS} files.')

    total = 0
    checked = []
    for upload in files:
        name = upload.name or 'file'
        ext = os.path.splitext(name)[1].lower()
        if ext not in _ALLOWED_TYPES:
            raise serializers.ValidationError(f'"{name}" isn\'t a JPG, PNG or PDF.')
        if upload.size > MAX_ATTACHMENT_BYTES:
            raise serializers.ValidationError(
                f'"{name}" is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.'
            )
        content_type, signatures = _ALLOWED_TYPES[ext]
        head = upload.read(8)
        upload.seek(0)
        if not any(head.startswith(sig) for sig in signatures):
            raise serializers.ValidationError(f'"{name}" doesn\'t look like a valid {ext[1:].upper()} file.')
        total += upload.size
        checked.append((upload, content_type))

    if total > MAX_TOTAL_ATTACHMENT_BYTES:
        raise serializers.ValidationError(
            f'Attachments add up to more than {MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} MB.'
        )
    return checked


class SiteSerializer(serializers.ModelSerializer):
    """The short site shape nested in tickets."""

    class Meta:
        model = Site
        fields = ['id', 'name', 'address']


class PortalSiteSerializer(serializers.ModelSerializer):
    """
    Client Sites page + site pickers: read with ticket and report counts, write
    name/address. (The same Site rows APS picks when writing solar reports.)

    Names are unique per client regardless of case, counting retired sites
    too (reusing a retired name would make old tickets and reports look like
    they belong to the new site).
    """

    ticket_count = serializers.IntegerField(read_only=True, default=0)
    open_ticket_count = serializers.IntegerField(read_only=True, default=0)
    report_count = serializers.IntegerField(read_only=True, default=0)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Site
        fields = ['id', 'name', 'address', 'ticket_count', 'open_ticket_count', 'report_count',
                  'created_by_name', 'created_at']
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {'address': {'required': False}}
        # Uniqueness is checked in validate_name (case-insensitive, client-scoped).
        validators = []

    def get_created_by_name(self, obj):
        # One login per client, so a site is either theirs or added by APS.
        return 'You' if obj.created_by_id and obj.created_by_id == obj.client_id else 'APS'

    def validate_name(self, value):
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('Give the site a name.')
        client = self.context['request'].user
        clash = Site.objects.filter(client=client, name__iexact=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        clash = clash.first()
        if clash and clash.is_active:
            raise serializers.ValidationError(f'You already have a site called "{clash.name}".')
        if clash:
            raise serializers.ValidationError(
                f'"{clash.name}" was retired. Ask APS to restore it, or use a different name.'
            )
        return value

    def validate_address(self, value):
        return ' '.join((value or '').split())


class TicketSerializer(serializers.ModelSerializer):
    """Read shape for the tickets list."""

    reference = serializers.CharField(read_only=True)
    urgency_label = serializers.CharField(source='get_urgency_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    status_group = serializers.CharField(read_only=True)
    site = SiteSerializer(read_only=True)
    attachment_count = serializers.IntegerField(read_only=True, default=0)
    # Chat messages from the other side the viewer hasn't seen (annotated by the view).
    unread_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Ticket
        fields = [
            'id',
            'reference',
            'title',
            'description',
            'service',
            'urgency',
            'urgency_label',
            'status',
            'status_label',
            'status_group',
            'site',
            'technician_name',
            'preferred_visit_date',
            'site_contact_name',
            'site_contact_phone',
            'attachment_count',
            'unread_count',
            'created_at',
            'updated_at',
        ]


class _AttachmentsMixin(serializers.Serializer):
    attachments = serializers.SerializerMethodField()

    def get_attachments(self, obj):
        request = self.context.get('request')
        out = []
        for a in obj.attachments.all():
            url = a.file.url if a.file else ''
            if url and request is not None:
                url = request.build_absolute_uri(url)
            out.append({'id': a.pk, 'name': a.original_name, 'url': url,
                        'content_type': a.content_type, 'size': a.size})
        return out


class ClientTicketDetailSerializer(_AttachmentsMixin, TicketSerializer):
    """One of the client's own tickets, with the files they attached."""

    class Meta(TicketSerializer.Meta):
        fields = [*TicketSerializer.Meta.fields, 'attachments']


class ServiceChoiceField(serializers.Field):
    """The new-ticket form's "What do you need?": the id of one of APS's real
    services (Site content → Services), checked against the live table. The
    ticket stores that service's name as text — it isn't linked to the table."""

    default_error_messages = {'invalid': 'Choose one of our services.'}

    def to_internal_value(self, data):
        try:
            return Service.objects.values_list('title', flat=True).get(pk=int(data))
        except (TypeError, ValueError, Service.DoesNotExist):
            self.fail('invalid')

    def to_representation(self, value):
        return value


class TicketCreateSerializer(serializers.ModelSerializer):
    """Write shape for the new-ticket form. The view supplies the client."""

    site = serializers.PrimaryKeyRelatedField(queryset=Site.objects.none())
    service = ServiceChoiceField()

    class Meta:
        model = Ticket
        fields = [
            'service',
            'title',
            'description',
            'site',
            'urgency',
            'preferred_visit_date',
            'site_contact_name',
            'site_contact_phone',
        ]
        extra_kwargs = {
            'preferred_visit_date': {'required': False, 'allow_null': True},
            'site_contact_name': {'required': False},
            'site_contact_phone': {'required': False},
            'urgency': {'required': False},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        client = self.context['request'].user
        # Only this client's live sites are choosable — another client's site
        # id is rejected as "invalid pk", so nothing leaks about it.
        self.fields['site'].queryset = Site.objects.filter(client=client, is_active=True)

    def validate_title(self, value):
        # One line: the title goes into email subjects, where a newline is illegal.
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('Give the job a short title.')
        return value

    def validate_description(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Describe the problem.')
        return value

    def validate_preferred_visit_date(self, value):
        if value and value < timezone.localdate():
            raise serializers.ValidationError('Pick today or a later date.')
        return value


# ── staff (dashboard admin) ──────────────────────────────────────────────────


def _client_name(user):
    try:
        return user.client_profile.company_name or user.username
    except Exception:  # noqa: BLE001
        return user.username


class StaffClientSerializer(serializers.Serializer):
    """The client a ticket belongs to, as the staff pages show it."""

    def to_representation(self, user):
        try:
            profile = user.client_profile
        except Exception:  # noqa: BLE001
            profile = None
        return {
            'id': user.pk,
            'name': _client_name(user),
            'username': user.username,
            'email': user.email,
            'email_verified': bool(profile and profile.email_verified),
            'phone': profile.phone if profile else '',
        }


class StaffTicketSerializer(TicketSerializer):
    """A ticket in the staff list: the client view plus its id and client."""

    client = StaffClientSerializer(read_only=True)

    class Meta(TicketSerializer.Meta):
        fields = [*TicketSerializer.Meta.fields, 'client']


class StaffTicketDetailSerializer(_AttachmentsMixin, StaffTicketSerializer):
    """One ticket with its attachments (for the staff ticket page)."""

    class Meta(StaffTicketSerializer.Meta):
        fields = [*StaffTicketSerializer.Meta.fields, 'attachments']


class StaffTicketUpdateSerializer(serializers.ModelSerializer):
    """What staff can change: the status and the technician shown to the client."""

    notify_client = serializers.BooleanField(required=False, default=True, write_only=True)

    class Meta:
        model = Ticket
        fields = ['status', 'technician_name', 'notify_client']
        extra_kwargs = {'status': {'required': False}, 'technician_name': {'required': False}}

    def validate_technician_name(self, value):
        return ' '.join((value or '').split())


# ── ticket chat ──────────────────────────────────────────────────────────────


class TicketMessageSerializer(serializers.ModelSerializer):
    """A chat message as either side sees it. ``context['viewer_role']`` is
    'client' or 'staff' and decides ``mine``."""

    author_name = serializers.SerializerMethodField()
    mine = serializers.SerializerMethodField()

    class Meta:
        model = TicketMessage
        fields = ['id', 'body', 'author_role', 'author_name', 'mine', 'created_at']

    def get_mine(self, obj):
        return obj.author_role == self.context.get('viewer_role')

    def get_author_name(self, obj):
        if obj.author_role == TicketMessage.Author.CLIENT:
            return _client_name(obj.ticket.client)
        # Staff: their first name and the company, e.g. "Thabo · APS"; never the
        # login username (often something internal like "admin").
        first = (obj.author.first_name or '').strip() if obj.author else ''
        return f'{first} · APS' if first else 'APS team'


class TicketMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MESSAGE_MAX_LENGTH, trim_whitespace=False)

    def validate_body(self, value):
        # Keep line breaks, drop leading/trailing blank space and NUL bytes.
        value = value.replace('\x00', '').strip()
        if not value:
            raise serializers.ValidationError('Write a message first.')
        return value
