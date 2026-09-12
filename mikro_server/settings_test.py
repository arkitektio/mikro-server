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
        # A non-admin user in the SAME organization, for delete-ownership tests:
        # "bot" satisfies the admin/bot mutation gate but is not an org admin, so
        # the creator/assignee guard actually applies to them.
        "bottest": {"sub": "2", "roles": ["bot"]},
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
