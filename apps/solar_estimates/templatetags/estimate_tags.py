"""Template tags for the estimate document (see templates/solar_estimates/estimate.html)."""

from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()

PENCIL = ('<svg viewBox="0 0 24 24" width="9" height="9" aria-hidden="true"><path fill="none" stroke="currentColor" '
          'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l'
          '1.5-5.5Z"/></svg>')


@register.simple_tag(takes_context=True)
def ed(context, key, value=None):
    """An editable value: plain text in the PDF; on the preview it gets a pencil button.

    ``value`` defaults to ``f[key]`` (the document's editable fields).
    """
    if value is None:
        value = context['f'].get(key, '')
    if not context.get('editable'):
        return conditional_escape(value)
    return format_html(
        '<span class="ed" data-edit="{}">{}</span><button type="button" class="pen" data-edit="{}" '
        'title="Edit" aria-label="Edit">{}</button>',
        key, value, key, mark_safe(PENCIL),
    )


@register.filter
def opt_key(option, part):
    return f"opt.{option['key']}.{part}"


@register.simple_tag(takes_context=True)
def pen(context, key):
    """Just a pencil (for a choice that has no text of its own, e.g. which option a sentence names)."""
    if not context.get('editable'):
        return ''
    return format_html('<button type="button" class="pen" data-edit="{}" title="Edit" aria-label="Edit">{}</button>',
                       key, mark_safe(PENCIL))
