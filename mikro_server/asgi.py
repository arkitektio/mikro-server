"""
ASGI config for the mikro service.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mikro_server.settings")
from django.core.asgi import get_asgi_application

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()


from django.conf import settings  # noqa: E402
from kante.router import router  # noqa: E402

from core import models  # noqa: E402
from embeddings.healer import ensure_healer_started  # noqa: E402

from .schema import schema  # noqa: E402

_routed_application = router(
    schema=schema,
    django_asgi_app=django_asgi_app,
    schema_path="schema",
)


# --- The embedding healer (``embeddings.healer``) --------------------------------------------
# Rows whose vector was produced by another embedding model (or none: written before
# embeddings were on, or while the weights were unreachable) are re-embedded by a loop inside
# THIS process, in row-locked batches -- no management command, no cron, no sidecar, and any
# number of replicas may run it side by side (``skip_locked`` keeps their claims disjoint).
#
# Daphne implements no ASGI ``lifespan``, but it installs its asyncio-backed Twisted reactor
# before importing this module, so ``callWhenRunning`` starts the loop the moment the server's
# event loop is up. The scope wrapper below is the server-agnostic fallback; both are
# idempotent. Off under the test suite (``EMBEDDINGS_HEALER_ENABLED``), which drives the
# healer explicitly.
_EMBEDDED_MODELS = (models.Folder, models.ArrayDataset, models.TableDataset)


def _start_healer() -> None:
    if getattr(settings, "EMBEDDINGS_HEALER_ENABLED", True):
        ensure_healer_started(_EMBEDDED_MODELS, interval=settings.EMBEDDINGS["SWEEP_INTERVAL"])


if "twisted.internet.reactor" in sys.modules:
    sys.modules["twisted.internet.reactor"].callWhenRunning(_start_healer)


async def application(scope, receive, send):  # noqa: ANN001, ANN201
    """The routed ASGI app, starting the healer on the first scope if nothing did before."""
    _start_healer()
    return await _routed_application(scope, receive, send)
