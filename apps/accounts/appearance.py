"""
Dashboard appearance saved to the account (staff and clients):

    {"theme": "default", "font": "inter", "density": "comfortable",
     "text": "md", "contrast": false, "motion": false}

The account is the source of truth; the dashboard keeps a localStorage copy
only to paint the right theme before it loads.

Theme ids are only checked for their FORMAT (lowercase letters, digits and
hyphens): the list of themes lives in the frontend (src/styles/dash-themes.json
+ dash-themes.css), so adding a theme needs no backend change, and the
dashboard falls back to Default for an id it doesn't know. Fonts, densities,
text sizes and the set of keys are checked strictly.
"""

import re

from rest_framework import serializers

from .models import AccountPreferences

DEFAULTS = {
    'theme': 'default',
    'font': 'inter',
    'density': 'comfortable',
    'text': 'md',
    'contrast': False,
    'motion': False,
}
FONTS = ('inter', 'dm-sans', 'nunito', 'plex', 'atkinson', 'system')
DENSITIES = ('compact', 'comfortable', 'spacious')
TEXT_SIZES = ('sm', 'md', 'lg', 'xl')
THEME_ID = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
THEME_MAX_LENGTH = 40


class _StrictBoolean(serializers.Field):
    """true / false only (no "yes", 1, "on" …)."""

    default_error_messages = {'invalid': 'Must be true or false.'}

    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail('invalid')
        return data

    def to_representation(self, value):
        return bool(value)


class AppearanceSerializer(serializers.Serializer):
    """Validates a full or partial appearance. Unknown keys are rejected."""

    theme = serializers.CharField(max_length=THEME_MAX_LENGTH, required=False, trim_whitespace=False)
    font = serializers.ChoiceField(choices=FONTS, required=False)
    density = serializers.ChoiceField(choices=DENSITIES, required=False)
    text = serializers.ChoiceField(choices=TEXT_SIZES, required=False)
    contrast = _StrictBoolean(required=False)
    motion = _StrictBoolean(required=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({'non_field_errors': ['Send the appearance as an object.']})
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError({key: ['Not an appearance setting.'] for key in unknown})
        if 'theme' in data and not isinstance(data['theme'], str):
            raise serializers.ValidationError({'theme': ['Must be a theme id.']})  # CharField would turn 7 into "7"
        return super().to_internal_value(data)

    def validate_theme(self, value):
        if not THEME_ID.match(value):
            raise serializers.ValidationError('Use lowercase letters, numbers and hyphens.')
        return value


def get_appearance(user):
    """The account's saved appearance (every key filled in), or None if it never saved one."""
    try:
        saved = user.dashboard_prefs.appearance
    except AccountPreferences.DoesNotExist:
        return None
    if not isinstance(saved, dict):
        return None
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_appearance(user, changes):
    """Merge validated ``changes`` over the saved appearance (or the defaults) and store it."""
    merged = {**(get_appearance(user) or DEFAULTS), **changes}
    AccountPreferences.objects.update_or_create(user=user, defaults={'appearance': merged})
    return merged
