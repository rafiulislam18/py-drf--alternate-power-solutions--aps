"""
Every permission a staff account can be given, grouped by dashboard section.

A permission's code is ``<section>.<action>`` (e.g. ``tickets.chat_send``).
Superusers have them all; other staff only what a superuser ticks on the
Staff accounts page. "Read" permissions (``view`` / ``chat_view``) are ticked
by default for a new staff account; everything else starts off.

Adding a permission: add it here, use it in the view (see
``apps.staff.permissions``), and gate the matching button on the frontend.
"""

SECTIONS = [
    {
        'key': 'reports', 'label': 'Solar dashboard — reports',
        'perms': [('view', 'View reports'), ('create', 'Create reports'),
                  ('edit', 'Edit reports'), ('delete', 'Delete reports')],
    },
    {
        'key': 'clients', 'label': 'Solar dashboard — clients & sites',
        'perms': [('view', 'View clients & their sites'), ('create', 'Create client accounts'),
                  ('edit', 'Add & edit sites'), ('delete', 'Retire / delete sites')],
    },
    {
        'key': 'tickets', 'label': 'Client tickets',
        # No "delete": tickets are never deleted.
        'perms': [('view', 'View tickets'), ('create', 'Create tickets'),
                  ('edit', 'Change ticket status'),
                  ('assign', 'Assign technician'),
                  ('chat_view', 'Read ticket chat'), ('chat_send', 'Send chat messages')],
    },
    {
        'key': 'quotes', 'label': 'Quote requests',
        'perms': [('view', 'View quote requests')],
    },
    {
        'key': 'estimates', 'label': 'Solar estimates',
        'perms': [('view', 'View proposals'), ('create', 'Create proposals'),
                  ('edit', 'Edit drafts & reopen proposals'), ('delete', 'Delete proposals'),
                  ('confirm', 'Confirm proposals & create the PDF'),
                  ('settings', 'Edit prices & settings')],
    },
    {
        'key': 'whatsapp', 'label': 'WhatsApp jobs',
        'perms': [('view', 'View messages'), ('edit', 'Mark messages as jobs / not a job'),
                  ('export', 'Send marked jobs to the sheet')],
    },
    {
        'key': 'services', 'label': 'Site content — services',
        'perms': [('view', 'View services'), ('create', 'Create services'),
                  ('edit', 'Edit services'), ('delete', 'Delete services')],
    },
    {
        'key': 'projects', 'label': 'Site content — projects',
        'perms': [('view', 'View projects'), ('create', 'Create projects'),
                  ('edit', 'Edit projects'), ('delete', 'Delete projects')],
    },
    {
        'key': 'blogs', 'label': 'Site content — blog (posts & categories)',
        'perms': [('view', 'View blog posts'), ('create', 'Create blog posts'),
                  ('edit', 'Edit blog posts'), ('delete', 'Delete blog posts')],
    },
    {
        # Staff only ever see (and restore) what they deleted themselves; only
        # superusers see and restore all of it. Nobody deletes things for good by hand.
        'key': 'trash', 'label': 'Trash',
        'perms': [('view', 'See & restore items you deleted')],
    },
    {
        'key': 'audit', 'label': 'Audit history',
        'perms': [('view_all', 'View the full audit history (everyone’s actions)')],
    },
]

LABELS = {f"{s['key']}.{action}": label for s in SECTIONS for action, label in s['perms']}
ALL_PERMS = list(LABELS)
READ_ACTIONS = ('view', 'chat_view')
DEFAULT_PERMS = [code for code in ALL_PERMS if code.split('.', 1)[1] in READ_ACTIONS]


def clean(codes):
    """Known codes only, in catalogue order, no duplicates."""
    wanted = set(codes or [])
    return [code for code in ALL_PERMS if code in wanted]
