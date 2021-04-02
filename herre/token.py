import re
import logging
from .settings import get_active_settings
import jwt

herre_settings = get_active_settings()


logger = logging.getLogger(__name__)

class JwtToken(dict):
    """
    Mimics the structure of `AbstractAccessToken` so you can use standard
    Django Oauth Toolkit permissions like `TokenHasScope`.
    """
    def __init__(self, decoded, user, token):
        decoded["scope"] = decoded["scope"] or ""
        self.scopes = decoded["scope"].split(" ")
        self.issuer = decoded["iss"]
        self.roles = decoded["roles"]
        self.app = decoded["client_app"]
        self.user = user
        self.type = decoded["type"]
        self.token = token
        super(JwtToken, self).__init__(**decoded, token=token, user=user)

    def __getattr__(self, item):
        return self[item]

    def is_valid(self, scopes=None):
        """
        Checks if the access token is valid.
        :param scopes: An iterable containing the scopes to check or None
        """
        return not self.is_expired() and self.allow_scopes(scopes)

    def is_expired(self):
        """
        Check token expiration with timezone awareness
        """
        # Token expiration is already checked
        return False

    def allow_scopes(self, scopes):
        """
        Check if the token allows the provided scopes
        :param scopes: An iterable containing the scopes to check
        """
        if not scopes:
            return True

        provided_scopes = set(self.scopes)
        resource_scopes = set(scopes)

        return resource_scopes.issubset(provided_scopes)


