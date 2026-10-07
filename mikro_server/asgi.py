"""
ASGI config for the mikro service.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mikro_server.settings")
from django.core.asgi import get_asgi_application

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

from embeddings import engine  # noqa: E402

# The embedding model is loaded here, as the server starts, rather than under the first row or
# query -- and nowhere else: a management command (``migrate``) never imports this module.
# Weights of another model or width are a broken image and stop the start; weights that cannot
# be loaded do not, ``search`` is lexical-only then (and says so on every query).
try:
    engine.warm_up()
except engine.EmbeddingsUnavailable as e:
    logging.getLogger(__name__).warning("%s. Rows are saved without a vector and search is lexical-only.", e)


from kante.router import router  # noqa: E402


from .schema import schema  # noqa: E402

_routed_application = router(
    schema=schema,
    django_asgi_app=django_asgi_app,
    schema_path="schema",
)

application = _routed_application
