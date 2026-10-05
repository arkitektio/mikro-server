"""System checks: a config file that says something this release does not read.

Imported from the app's ``ready()`` to register them. ``manage.py migrate`` runs at every boot,
so the warnings are in every container's log before it serves.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig
from django.core.checks import CheckMessage, Warning, register

from mikro_server import configuration


@register()
def check_unread_configuration(app_configs: Sequence[AppConfig] | None, **kwargs: object) -> list[CheckMessage]:
    """``mikro.W001``: a key no setting claims. ``mikro.W002``: a key read under a former name."""
    found = configuration.unread()
    return [
        *(Warning(f"The configuration sets `{key}`, which this release does not read.", hint="A misspelling, or a key of another release: see CONFIG.md.", id="mikro.W001") for key in found.unknown),
        *(Warning(f"The configuration sets `{key}`, which is now `{now}`.", hint="Still read; the next major release will not.", id="mikro.W002") for key, now in found.renamed),
    ]
