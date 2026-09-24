from .settings import *  # noqa
from .settings import DATABASES, AUTHENTIKATE, DATALAYER
import logging
import os

# There is no STS to assume a role against under unit tests, and a grant that cannot be scoped
# now refuses rather than quietly returning this service's permanent key. Tests that exercise a
# grant care about its *shape*, not its credentials, so let them have the unscoped one --
# `test_datalayer_grants` covers the scoping itself with a stubbed STS client.
DATALAYER = {**DATALAYER, "allow_unscoped_fallback": True}

# The test stack publishes postgres on an *ephemeral* host port, so there is no port
# to hardcode here: docker picks one per run, and `tests/conftest.py`'s
# `django_db_modify_db_settings` overwrites PORT below with what it picked, before
# pytest-django creates the test database. This value is only the fallback for
# running a `manage.py` command against a stack you started by hand -- set
# MIKRO_TEST_DB_PORT to whatever `docker compose port db 5432` reports for it.
DATABASES["default"] = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "testdb",
    "USER": "test",
    "PASSWORD": "test",
    "HOST": "localhost",
    "PORT": os.environ.get("MIKRO_TEST_DB_PORT", "5555"),
}


# Django forces DEBUG=False under the test runner, and authentikate 3.0 refuses static
# tokens when DEBUG is False. These are deliberate test fixtures, so opt in explicitly.
AUTHENTIKATE = {
    **AUTHENTIKATE,
    "allow_static_tokens_in_production": True,
    "static_tokens": {
        "test": {"sub": "1"},
        # A user in a different organization, for cross-tenant scoping tests.
        "othertest": {"sub": "9", "org": "other_org"},
        # The SAME user as "test", acting in the other organization: a user who belongs to
        # two orgs must only ever see the rows of the org they are acting in.
        "test-other-org": {"sub": "1", "org": "other_org"},
        # A non-admin user in the SAME organization, for delete-ownership tests:
        # "bot" satisfies the admin/bot mutation gate but is not an org admin, so
        # the creator/assignee guard actually applies to them.
        "bottest": {"sub": "2", "roles": ["bot"]},
        # An "editor": writes data and requests upload grants, but is not an org admin.
        "editortest": {"sub": "3", "roles": ["editor"]},
        # A "viewer": in the org, but holding no role that may write or upload.
        "viewertest": {"sub": "4", "roles": ["viewer"]},
    },
}


# Disable migrations for faster tests
class DisableMigrations:
    """Disable migrations during testing for faster test execution."""

    def __contains__(self, item: str) -> bool:
        """Check if item is in migration modules."""
        return True

    def __getitem__(self, item: str) -> None:
        """Get migration module for item."""
        return None


MIGRATION_MODULES = DisableMigrations()

# Disable logging during tests to reduce noise
logging.disable(logging.CRITICAL)

# Enable database access from async code in tests
DATABASE_ROUTERS = []

# Use in-memory channel layer for tests instead of Redis
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# The embedding healer re-embeds stale rows in the background. Tests call
# ``embeddings.healer.reembed_stale`` explicitly instead, so a pass can never race an assertion.
EMBEDDINGS_HEALER_ENABLED = False
