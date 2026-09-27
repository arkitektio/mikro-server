"""
ASGI config for the mikro service.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mikro_server.settings")
from django.core.asgi import get_asgi_application

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()


from kante.router import router  # noqa: E402


from .schema import schema  # noqa: E402

_routed_application = router(
    schema=schema,
    django_asgi_app=django_asgi_app,
    schema_path="schema",
)


# Nothing loops in here: stale embeddings are healed by the ``reembed_stale`` action the hub's
# rekuest schedules (``mikro_server/service.py``, vendored ``rekuest_service``).
application = _routed_application
