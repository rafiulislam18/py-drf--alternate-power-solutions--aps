from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import GasGuardUser
from .tokens import GasGuardRefreshToken

User = GasGuardUser

# A throwaway valid password hash. When login is attempted for an email that
# doesn't exist, we still run the hasher against THIS (instead of the user's
# real hash) so the request takes roughly the same time either way — blunting
# timing attacks that probe which emails are registered. It must be a real hash
# string: ``check_password(pw, None)`` raises TypeError, which would 500 the
# request and become a perfect enumeration oracle.
_DUMMY_PASSWORD_HASH = make_password('timing-attack-mitigation-dummy')


class UserSerializer(serializers.ModelSerializer):
    """Public representation of a user — never exposes the password."""

    notifications = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'first_name',
            'last_name',
            'tier',
            'notifications',
            'is_staff',
            'date_joined',
        ]
        read_only_fields = ['id', 'tier', 'is_staff', 'date_joined']

    def get_notifications(self, obj):
        return {
            'emailEnabled': obj.email_alerts_enabled,
            'notifyEmail': obj.notify_email or obj.email,
        }


class NotificationPrefsSerializer(serializers.ModelSerializer):
    """Writable low-gas alert preferences, keyed as the frontend sends them."""

    emailEnabled = serializers.BooleanField(source='email_alerts_enabled')
    notifyEmail = serializers.EmailField(
        source='notify_email', allow_blank=True, required=False
    )

    class Meta:
        model = User
        fields = ['emailEnabled', 'notifyEmail']


class RegisterSerializer(serializers.Serializer):
    """Validates a self-service sign-up (email verification pending).

    No ``GasGuardUser`` is created here — registration only stashes a
    ``PendingRegistration`` and emails a code; the account is created on
    successful verification. So this is a plain Serializer, not a
    ModelSerializer, and it enforces a unique-email check itself.
    """

    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)

    def validate_email(self, value):
        # Normalise so casing can't slip a duplicate past the unique check.
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                'This email is already registered. Please sign in instead.'
            )
        return value


class VerifyEmailSerializer(serializers.Serializer):
    """A registration verification attempt: the email plus its 6-digit code."""

    email = serializers.EmailField()
    code = serializers.CharField(max_length=6, min_length=6)

    def validate_email(self, value):
        return value.strip().lower()


class ResendVerificationSerializer(serializers.Serializer):
    """Request a fresh verification code for a pending registration."""

    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class EmailTokenObtainPairSerializer(serializers.Serializer):
    """Login serializer — validates email + password against ``GasGuardUser``.

    Gas Guard is not the project ``AUTH_USER_MODEL``, so SimpleJWT's built-in
    ``TokenObtainPairSerializer`` (which authenticates the swappable auth user)
    can't be used. We verify the credentials directly against the Gas Guard user
    table and issue a Gas-Guard-scoped token pair.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    default_error_messages = {
        'no_active_account': 'No active account found with the given credentials.'
    }

    def validate(self, attrs):
        email = attrs.get('email', '').strip().lower()
        password = attrs.get('password', '')

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # Run the hasher anyway (against a dummy hash) to blunt timing
            # attacks that probe which emails exist.
            check_password(password, _DUMMY_PASSWORD_HASH)
            self.fail('no_active_account')

        if not user.is_active or not user.check_password(password):
            self.fail('no_active_account')

        refresh = GasGuardRefreshToken.for_user(user)
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': UserSerializer(user).data,
        }
