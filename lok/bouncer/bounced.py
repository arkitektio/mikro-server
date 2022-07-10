from ..enums import LokGrantType
from ..token import JwtToken
import logging
from django.contrib.sessions.backends.db import SessionStore
import asyncio

class BounceException(Exception):
    pass

logger = logging.getLogger(__name__)

SESSION_APP = None


def get_session_app():
    global SESSION_APP
    if SESSION_APP is None:
        from ..models import LokApp
        SESSION_APP, created = LokApp.objects.get_or_create(client_id="session", name="Session", grant_type=LokGrantType.SESSION.value)
    
    return SESSION_APP


class Bounced:

    def __init__(self, user, app, scopes, token=None, is_jwt=False) -> None:
        self._user = user
        self._app = app
        self._scopes = scopes or []
        self.scopeset = set(self._scopes)
        self.is_jwt = is_jwt
        self.token = token

    @property
    def user(self):
        return self._user

    @property
    def app(self):
        return self._app

    @property
    def scopes(self):
        return self._scopes

    @property
    def roles(self):
        return self.user.roles


    def bounce(self, required_roles=[], required_scopes=[], anonymous=False, only_jwt=False):

        required_scopes = set(required_scopes)
        required_roles = set(required_roles)

        if only_jwt and not self.is_jwt:
            raise BounceException("Only Apps authorized via JWT are allowed here")
       
        if self._app.grant_type == LokGrantType.CLIENT_CREDENTIALS.value:
            if not required_scopes.issubset(self.scopeset):
                raise BounceException(f"App has not the required Scopes. Required {required_scopes}")

        if self._app.grant_type == LokGrantType.IMPLICIT.value:
            if not anonymous and self.user.is_anonymous:
                raise BounceException("Only signed in users are allowed here")
    
        # Scope tests
        if self._app.grant_type == LokGrantType.PASSWORD.value:
            return True


    @classmethod
    def from_auth(cls, auth: JwtToken):
        return cls(auth.user, auth.app, auth.scopes, token=auth.token, is_jwt=True)

    @classmethod
    def from_session_app_and_user(cls, sessionApp, user):
        return cls(user, sessionApp, [])