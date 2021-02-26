from herre.token import JwtToken
import logging
from django.contrib.sessions.backends.db import SessionStore
class BounceException(Exception):
    pass

logger = logging.getLogger(__name__)


class Bounced:

    def __init__(self, user, scopes, roles, type) -> None:

        self._user = user
        self._scopes = scopes
        self._roles = roles
        self._type = "m2m" if type == "client-credentials" else "u2m"


        self.scopeset = set(self._scopes)
        self.roleset = set(self._roles)
        logger.info(f"Bounced Context of Type {self._type} created")

    @property
    def user(self):
        return self._user

    @property
    def scopes(self):
        return self._scopes

    @property
    def roles(self):
        return self._roles


    def bounce(self, required_roles=[], required_scopes=[], allowed_types = ["m2m","u2m"], anonymous=False):

        required_scopes = set(required_scopes)
        required_roles = set(required_roles)

        if not anonymous and self.user.is_anonymous:
            raise BounceException("Only signed in users are allowed here")
        if self._type not in allowed_types:
            raise BounceException("Wrong App type")
        if not required_scopes.issubset(self.scopeset):
            raise BounceException("App has not the required Scopes")
        if not required_roles.issubset(self.roleset):
            raise BounceException("User has not the required Roles")
        


    @classmethod
    def from_auth(cls, auth: JwtToken):
        return cls(auth.user, auth.scopes, auth.roles, auth.type)

    @classmethod
    def from_session_and_user(cls, session: SessionStore, user):
        return cls(user, [], [], "u2m")