"""Validate this service's configuration without starting the app.

Re-loads the typed settings schema (the same one Django builds at boot) from
``config.yaml`` + environment variables, then prints the fully resolved
configuration as a tree with secrets redacted. Exits non-zero with a
field-by-field report when the configuration is invalid.

    python manage.py validate_settings
    python manage.py validate_settings --strict

``--strict`` is what an installer asks before it moves a hub to this release: it exits
``78`` (``EX_CONFIG``) when the configuration sets a key this release does not read — the one
mistake nothing else reports. A key still read under a former name is said and is no failure:
a release may rename a key within its major. Any other non-zero exit is not that answer: an
invalid configuration fails every command at start, this one included, with ``1``.

Honors ``ARKITEKT_CONFIG_FILE`` to point at an alternate YAML file.
"""

from __future__ import annotations


from django.core.management.base import BaseCommand, CommandParser
from pydantic import ValidationError
from rich.console import Console
from rich.tree import Tree

from mikro_server.configuration import Settings, config_path, unread

# ``--strict``'s no: the configuration sets a key this release does not read (sysexits' EX_CONFIG).
NOT_READ = 78

# Leaf keys whose values are secrets and must never be printed in the clear.
SECRET_HINTS = ("password", "secret_key", "secret", "private_key", "access_key")


def _is_secret(key: object) -> bool:
    name = str(key).lower()
    return any(hint in name for hint in SECRET_HINTS)


def _mask(value: object) -> str:
    if isinstance(value, str):
        return f"**** (len={len(value)})"
    return "****"


def _add(tree: Tree, data: object, *, secret: bool = False) -> None:
    """Recursively render ``data`` (a ``model_dump()`` result) into ``tree``.

    ``secret`` masks every leaf below this point (a secret-named block); leaves
    whose own key looks secret are masked individually.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            key_secret = secret or _is_secret(key)
            if isinstance(value, (dict, list)):
                branch = tree.add(f"[bold cyan]{key}[/bold cyan]")
                _add(branch, value, secret=key_secret)
            else:
                shown = _mask(value) if key_secret else repr(value)
                tree.add(f"[cyan]{key}[/cyan]: {shown}")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, (dict, list)):
                branch = tree.add(f"[bold cyan]item {index}[/bold cyan]")
                _add(branch, item, secret=secret)
            else:
                tree.add(_mask(item) if secret else repr(item))
    else:
        tree.add(_mask(data) if secret else repr(data))


class Command(BaseCommand):
    help = "Validate the service configuration (YAML + env) and print the resolved, redacted settings."
    # A config validator needs no model/URL/DB system checks (and they may fail
    # independently of config); skip them so only configuration is exercised.
    requires_system_checks: list = []

    def add_arguments(self, parser: CommandParser) -> None:
        """``--strict``: a key that is not read as written fails the command."""
        parser.add_argument("--strict", action="store_true", help="Also fail on keys this release does not read as written.")

    def handle(self, *args: object, **options: object) -> None:
        console = Console()
        path = config_path()
        try:
            settings = Settings()
        except ValidationError as exc:
            console.print(f"[bold red]Invalid configuration[/bold red] (source: {path})")
            for err in exc.errors():
                loc = ".".join(str(part) for part in err["loc"])
                console.print(f"  [red]{loc}[/red]: {err['msg']}")
            raise SystemExit(1)

        tree = Tree(f"[bold green]Configuration valid[/bold green] (source: {path})")
        _add(tree, settings.model_dump())
        console.print(tree)

        found = unread()
        for key in found.unknown:
            console.print(f"[yellow]not read[/yellow]: {key}")
        for key, now in found.renamed:
            console.print(f"[yellow]renamed[/yellow]: {key} is now {now}")
        if found.unknown and options["strict"]:
            console.print(f"[bold red]{path} sets keys this release does not read[/bold red]")
            raise SystemExit(NOT_READ)
