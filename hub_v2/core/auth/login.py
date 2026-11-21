"""Login by username or by e-mail (N-205)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """The username as always; an e-mail when it is no one's username and belongs to exactly one person."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username and "@" in username:
            User = get_user_model()
            if not User._default_manager.filter(**{User.USERNAME_FIELD: username}).exists():
                matches = list(User._default_manager.filter(email__iexact=username.strip())[:2])
                if len(matches) == 1:
                    username = matches[0].get_username()
        return super().authenticate(request, username=username, password=password, **kwargs)
