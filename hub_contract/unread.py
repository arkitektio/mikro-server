"""What a config document says that a release does not read as written.

A setting nobody reads is silent by nature: the service starts, with the default. This is what
makes it loud — a key no setting claims, or one still read under a former name.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping

from pydantic import AliasChoices, BaseModel
from pydantic_settings import BaseSettings

from hub_contract.json_types import JSON


@dataclasses.dataclass(frozen=True)
class Unread:
    """What a config says that a release does not read as written."""

    unknown: list[str]
    """Keys no setting claims, as dotted paths: a misspelling, or a key of another release."""
    renamed: list[tuple[str, str]]
    """Keys still read under a former name, with the name they have now."""

    def __bool__(self) -> bool:
        """Whether there is anything to say."""
        return bool(self.unknown or self.renamed)


def _models_of(annotation: object, module: str) -> list[type[BaseModel]]:
    """The settings models of ``module`` an annotation holds: itself, or inside ``Optional`` / ``list``.

    Only that module's: a block another package defines is that package's to judge, and its
    aliases are spellings, not former names.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation] if annotation.__module__ == module else []
    inside = typing.cast("tuple[object, ...]", typing.get_args(annotation))
    return [model for inner in inside for model in _models_of(inner, module)]


def _names(model: type[BaseModel]) -> dict[str, str]:
    """Every key ``model`` reads, to the field's own name: its fields and their former names."""
    names: dict[str, str] = {}
    for name, field in model.model_fields.items():
        names[name] = name
        alias = field.validation_alias
        for former in alias.choices if isinstance(alias, AliasChoices) else [alias]:
            if isinstance(former, str):
                names[former] = name
    return names


def _unread(model: type[BaseModel], written: Mapping[str, JSON], path: str, module: str, into: Unread) -> None:
    # A block that passes its extras on (a connection's driver options) and the top level,
    # which every service of a hub shares the shape of, are open: nothing there is unknown.
    closed = model.model_config.get("extra") != "allow" and not issubclass(model, BaseSettings)
    names = _names(model)
    for key, value in written.items():
        where = f"{path}{key}"
        name = names.get(key)
        if name is None:
            if closed:
                into.unknown.append(where)
            continue
        if name != key:
            into.renamed.append((where, f"{path}{name}"))
        for inner in _models_of(model.model_fields[name].annotation, module):
            for index, item in enumerate(value) if isinstance(value, list) else [(None, value)]:
                if isinstance(item, dict):
                    _unread(inner, item, f"{where}." if index is None else f"{where}[{index}].", module, into)


def unread(settings: type[BaseSettings], written: Mapping[str, JSON]) -> Unread:
    """What ``written`` says that ``settings`` does not read as written."""
    found = Unread(unknown=[], renamed=[])
    _unread(settings, written, "", settings.__module__, found)
    return found
