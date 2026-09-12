import os
import time

import boto3
import psycopg
import pytest
from moto import mock_aws

from authentikate.models import Client, Organization, User, Membership
from django.contrib.contenttypes.management import create_contenttypes
from django.db.models.signals import post_migrate
from kante.context import HttpContext, UniversalRequest
from strawberry.http.temporal_response import TemporalResponse
from dokker import PortNotFoundError, testing





@pytest.fixture(scope="function")
def s3(aws_credentials):
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")



@pytest.fixture(scope="session")
def backend_stack():
    """Bring up the integration stack and yield the host port its postgres landed on.

    The port is *not* fixed. The compose file publishes 5432 with no host port, so
    docker assigns a free one per run and this asks the running stack which it got.
    That is the whole isolation story: dokker mints a unique compose project name per
    run, but a pinned host port defeats it -- two projects with different names still
    cannot both bind one host port. Every suite under mounts/ used to pin 5555/6666,
    so any two of them running at once, and any stack stranded by a crashed run,
    broke the next run with "port is already allocated".

    The port is yielded rather than written to a module global because
    `django_db_modify_db_settings` needs it to point Django at this stack *before*
    pytest-django creates the test database -- see that fixture below.
    """
    docker_compose_path = os.path.join(os.path.dirname(__file__), "integration", "docker-compose.yaml")

    # No `down()` before `up()`: `testing()` mints a fresh `dokker-test-<uuid>` project
    # every call, so downing it would only tear down the empty project this run just
    # named, never a predecessor. It read as protection and was a no-op.
    with testing(docker_compose_path) as e:
        e.up()

        # Ask the running stack, not the compose file: `get_port` shells out to
        # `docker compose port`, which is the only thing that knows what docker picked.
        #
        # Resolved *inside* the retry loop, not before it. `up()` is not called with
        # `wait`, so it can return before the container is running -- and compose
        # prints nothing for a container that is not up yet, which dokker turns into
        # PortNotFoundError. Retrying only the connect would make that race a hard
        # failure a fifth of a second before it would have succeeded.
        db_port = None

        deadline = time.monotonic() + 30
        while True:
            try:
                if db_port is None:
                    db_port = e.get_port("db", 5432)
                with psycopg.connect(
                    dbname="testdb",
                    user="test",
                    password="test",
                    host="localhost",
                    port=db_port,
                    connect_timeout=1,
                ) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                break
            except (psycopg.OperationalError, PortNotFoundError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

        # The suite builds its schema with run-syncdb (migrations disabled), so the
        # cube extension migration never runs here -- but CREATE TABLE for
        # Annotation.bbox_cube needs the type to exist. Install it into template1
        # so the test database pytest-django creates from it inherits it, and into
        # testdb itself for anything connecting directly.
        for dbname in ("template1", "testdb"):
            with psycopg.connect(
                dbname=dbname,
                user="test",
                password="test",
                host="localhost",
                port=db_port,
                autocommit=True,
            ) as connection:
                connection.execute("CREATE EXTENSION IF NOT EXISTS cube")

        yield db_port


@pytest.fixture(scope="session")
def django_db_modify_db_settings(backend_stack):
    """Start the backend services, and point Django at the port they came up on.

    pytest-django calls this before creating the test database, which is the only
    window in which the port can be set: `settings_test` is imported long before any
    fixture runs, so it cannot know a port docker had not assigned yet. Its `PORT` is
    a placeholder for a hand-started stack (see MIKRO_TEST_DB_PORT there); under
    pytest this line is what actually decides where the connection goes.
    """
    from django.conf import settings

    settings.DATABASES["default"]["PORT"] = str(backend_stack)
    yield


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    # Every transaction=True test teardown flushes the DB and re-fires
    # post_migrate, which rebuilds all contenttypes and permissions from the
    # model registry (~1s per test). The rows never change between tests, so
    # snapshot them once and swap the rebuild for a bulk re-insert with the
    # original pks (keeps guardian FKs and the ContentType pk cache valid).
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    with django_db_blocker.unblock():
        contenttypes = list(ContentType.objects.all())
        permissions = list(Permission.objects.all())

    post_migrate.disconnect(dispatch_uid="django.contrib.auth.management.create_permissions")
    post_migrate.disconnect(create_contenttypes)

    def restore_contenttypes_and_permissions(sender, **kwargs):
        # post_migrate fires once per app config on flush; restore once.
        if getattr(sender, "label", None) != "contenttypes":
            return
        ContentType.objects.bulk_create(contenttypes, ignore_conflicts=True)
        Permission.objects.bulk_create(permissions, ignore_conflicts=True)

    post_migrate.connect(
        restore_contenttypes_and_permissions,
        dispatch_uid="tests.restore_contenttypes_and_permissions",
    )
    yield

    # The async tests run sync ORM code in asgiref's executor threads, whose
    # connections outlive the tests and block dropping the test database
    # ("database is being accessed by other users"). Kill them before
    # pytest-django's teardown drops the database.
    from django.db import connections

    with django_db_blocker.unblock():
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        connections.close_all()


@pytest.fixture(scope="function")
def authenticated_context(db, backend_stack):
    # Match the identity the static "test" token resolves to (see settings_test
    # STATIC_TOKENS + authentikate's token expansion), so the org/user on this
    # context is the same one the schema's AuthentikateExtension authenticates as
    # at resolve time — otherwise organization-scoped queries see no data.
    user, _ = User.objects.get_or_create(
        sub="1", iss="static_issuer", defaults={"username": "static_issuer_1"}
    )
    client, _ = Client.objects.get_or_create(client_id="oinsoins")
    org, _ = Organization.objects.get_or_create(slug="static_org")
    membership, _ = Membership.objects.get_or_create(
        user=user,
        organization=org,
    )

    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=client,  # type: ignore
        _user=user,  # type: ignore
        _organization=org,  # type: ignore
    )
    request.set_membership(membership)  # type: ignore

    return HttpContext(request=request, response=TemporalResponse(), headers={"Authorization": "Bearer test"}, type="http")

@pytest.fixture(scope="function")
def bot_context(db, backend_stack) -> HttpContext:
    """A non-admin user (static token "bottest") in the SAME org as authenticated_context.

    Holds the "bot" role: it passes the admin/bot mutation gate but is not an org
    admin, so the creator/assignee delete guard actually applies to it — the
    context used to exercise the denial path.
    """
    user, _ = User.objects.get_or_create(
        sub="2", iss="static_issuer", defaults={"username": "static_issuer_2"}
    )
    client, _ = Client.objects.get_or_create(client_id="oinsoins")
    org, _ = Organization.objects.get_or_create(slug="static_org")
    membership, _ = Membership.objects.get_or_create(
        user=user,
        organization=org,
        defaults={"roles": ["bot"]},
    )

    request = UniversalRequest(
        _extensions={"token": "bottest"},
        _client=client,  # type: ignore
        _user=user,  # type: ignore
        _organization=org,  # type: ignore
    )
    request.set_membership(membership)  # type: ignore

    return HttpContext(request=request, response=TemporalResponse(), headers={"Authorization": "Bearer bottest"}, type="http")


@pytest.fixture(scope="function")
def other_org_context(db, backend_stack) -> HttpContext:
    """A context for a user in a different organization (static token "othertest")."""
    user, _ = User.objects.get_or_create(
        sub="9", iss="static_issuer", defaults={"username": "static_issuer_9"}
    )
    client, _ = Client.objects.get_or_create(client_id="oinsoins")
    org, _ = Organization.objects.get_or_create(slug="other_org")
    membership, _ = Membership.objects.get_or_create(
        user=user,
        organization=org,
    )

    request = UniversalRequest(
        _extensions={"token": "othertest"},
        _client=client,  # type: ignore
        _user=user,  # type: ignore
        _organization=org,  # type: ignore
    )
    request.set_membership(membership)  # type: ignore

    return HttpContext(request=request, response=TemporalResponse(), headers={"Authorization": "Bearer othertest"}, type="http")


@pytest.fixture(scope="function")
def simple_api_context(db, backend_stack) -> HttpContext:
    user, _ = User.objects.get_or_create(
        sub="1", iss="static_issuer", defaults={"username": "static_issuer_1"}
    )
    client, _ = Client.objects.get_or_create(client_id="oinsoins")
    org, _ = Organization.objects.get_or_create(slug="static_org")
    membership, _ = Membership.objects.get_or_create(
        user=user,
        organization=org,
    )

    request = UniversalRequest(
        _extensions={"token": "test"},
        _client=client,  # type: ignore
        _user=user,  # type: ignore
        _organization=org,  # type: ignore
    )
    request.set_membership(membership)  # type: ignore

    return HttpContext(request=request, response=TemporalResponse(), headers={"Authorization": "Bearer test"}, type="http")
