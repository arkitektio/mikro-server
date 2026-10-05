"""The verbs an installer runs in a service's image: ``python -m hub_contract <verb>``.

``describe``
    Prints the service's :class:`~hub_contract.description.Description` as JSON. Needs no
    config. An image that cannot answer this has no contract, and an installer treats it as it
    treated images before there was one.

``render [--facts FILE] [--overrides FILE]``
    Prints this release's config, as YAML, written from a hub's facts (``/hub/facts.yaml``)
    with what the hub's operator set laid over it (``/hub/overrides.yaml``, if there). The
    result is loaded the way the service loads it at start before it is printed, so what comes
    out is a config this release starts on.

``check [--config FILE]``
    Judges a config as it stands — the file the service would start on.

``migrate [--plan]``
    The release's database migrations (``manage.py migrate``); ``--plan`` lists what would run.

``upgrade --from A --to B``
    What the release does to its data between two versions (``manage.py upgrade``), if it
    ships anything of the kind.

Exit codes: ``0`` done. ``78`` (``EX_CONFIG``) is this release's own no — facts it cannot be
configured from, an override it does not read, an invalid config — with the reason on stderr,
and nothing on stdout. Anything else is the command failing.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import typing
from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from hub_contract.contract import Contract, Refused, load
from hub_contract.facts import Facts
from hub_contract.json_types import JSON
from hub_contract.merge import merge
from hub_contract.unread import unread

#: The release's own no (sysexits' EX_CONFIG).
REFUSED = 78

FACTS = "/hub/facts.yaml"
OVERRIDES = "/hub/overrides.yaml"
#: What every service reads its config file's path from.
CONFIG_FILE = "ARKITEKT_CONFIG_FILE"


class No(Exception):
    """This release's refusal, as the lines to say."""

    def __init__(self, headline: str, reasons: Sequence[str]) -> None:
        """What is refused, and each reason."""
        super().__init__(headline)
        self.headline = headline
        self.reasons = list(reasons)


def _document(path: Path, what: str) -> dict[str, JSON]:
    """A YAML file that holds a mapping."""
    try:
        loaded = typing.cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as error:
        raise No(f"{what} could not be read", [f"{path}: {error}"]) from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise No(f"{what} is not a mapping", [str(path)])
    return typing.cast("dict[str, JSON]", loaded)


def _errors(error: ValidationError) -> list[str]:
    return [f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}" for problem in error.errors()]


def judge(contract: Contract, document: dict[str, JSON]) -> None:
    """Refuse a config this release does not read as written, or does not start on.

    Loaded exactly as at start: from a file named by ``ARKITEKT_CONFIG_FILE``, with the
    environment over it.
    """
    found = unread(contract.settings, document)
    if found.unknown:
        raise No("this release does not read", found.unknown)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as file:
        yaml.safe_dump(document, file, sort_keys=False)
        file.flush()
        before = os.environ.get(CONFIG_FILE)
        os.environ[CONFIG_FILE] = file.name
        try:
            contract.settings()
        except ValidationError as error:
            raise No("this release cannot start on that config", _errors(error)) from error
        finally:
            if before is None:
                del os.environ[CONFIG_FILE]
            else:
                os.environ[CONFIG_FILE] = before


def render(contract: Contract, facts: Path, overrides: Path) -> dict[str, JSON]:
    """This release's config from the facts at ``facts``, with ``overrides`` laid over if there."""
    try:
        said = Facts.model_validate(_document(facts, "the hub's facts"))
    except ValidationError as error:
        raise No("this release does not understand the hub's facts", _errors(error)) from error
    try:
        document = contract.render(said)
    except Refused as error:
        raise No("this release cannot be configured for this hub", [str(error)]) from error
    if overrides.is_file():
        document = merge(document, _document(overrides, "what the operator set"))
    judge(contract, document)
    return document


def _manage(*arguments: str) -> int:
    """Hand over to the service's own ``manage.py``: its exit code is the answer."""
    os.execvp(sys.executable, [sys.executable, "manage.py", *arguments])


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one verb; the exit code is its answer."""
    parser = argparse.ArgumentParser(prog="python -m hub_contract", description="What this service's image answers a hub's installer.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("describe", help="What the service needs from a hub and offers to it, as JSON.")
    rendering = verbs.add_parser("render", help="This release's config, from a hub's facts.")
    rendering.add_argument("--facts", type=Path, default=Path(FACTS))
    rendering.add_argument("--overrides", type=Path, default=Path(OVERRIDES))
    checking = verbs.add_parser("check", help="Whether this release reads a config as written.")
    checking.add_argument("--config", type=Path, default=None)
    migrating = verbs.add_parser("migrate", help="The release's database migrations.")
    migrating.add_argument("--plan", action="store_true", help="List what would run, and run nothing.")
    upgrading = verbs.add_parser("upgrade", help="What the release does to its data between two versions.")
    upgrading.add_argument("--from", dest="left", required=True)
    upgrading.add_argument("--to", dest="reached", required=True)
    asked = parser.parse_args(arguments)

    contract = load()
    verb: str = asked.verb  # pyright: ignore[reportAny]  argparse's namespace
    try:
        if verb == "describe":
            print(contract.description.model_dump_json(indent=2))
        elif verb == "render":
            facts: Path = asked.facts  # pyright: ignore[reportAny]
            overrides: Path = asked.overrides  # pyright: ignore[reportAny]
            print(yaml.safe_dump(render(contract, facts, overrides), sort_keys=False), end="")
        elif verb == "check":
            given: Path | None = asked.config  # pyright: ignore[reportAny]
            path = given or Path(os.environ.get(CONFIG_FILE, "config.yaml"))
            judge(contract, _document(path, "the config"))
        elif verb == "migrate":
            plan: bool = asked.plan  # pyright: ignore[reportAny]
            return _manage("migrate", "--plan") if plan else _manage("migrate", "--noinput")
        elif verb == "upgrade":
            left: str = asked.left  # pyright: ignore[reportAny]
            reached: str = asked.reached  # pyright: ignore[reportAny]
            if contract.upgrades:
                return _manage("upgrade", "--from", left, "--to", reached)
            print(f"Nothing to upgrade between {left} and {reached}.")
    except No as refusal:
        print(f"{contract.description.name}: {refusal.headline}:", file=sys.stderr)
        for reason in refusal.reasons:
            print(f"  {reason}", file=sys.stderr)
        return REFUSED
    return 0
