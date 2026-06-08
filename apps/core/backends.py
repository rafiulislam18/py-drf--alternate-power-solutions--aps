from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    """Authenticate by username case-insensitively.

    Client request: a user with username "urban" should be able to log in as
    "Urban", "URBan", etc. Django's default ModelBackend matches username
    case-sensitively, so we override the lookup to use `username__iexact`.

    Uniqueness is enforced case-insensitively at account creation
    (see CreateClientUserSerializer.validate_username), so a normal lookup
    returns at most one user. We still guard against the edge case of legacy
    duplicates by failing closed instead of authenticating an arbitrary match.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        try:
            user = UserModel._default_manager.get(
                **{f'{UserModel.USERNAME_FIELD}__iexact': username}
            )
        except UserModel.DoesNotExist:
            # Run the default password hasher to mitigate timing attacks that
            # could reveal whether a username exists.
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            # Legacy duplicates differing only by case — ambiguous, fail closed.
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
