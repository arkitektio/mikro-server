"""Shape -> shape lineage: which shapes a new shape was made from, and how.

The one reader of a shape's ``derivedFrom`` and the one writer of
:class:`~core.models.AnnotationDerivation`. Container lineage is the coordinate graph
(``core.logic.coordinate_system.write_derivation_edges``); a shape made from a shape is
not, for the reasons the model gives -- so this module writes no edge, ever.

Everything is resolved before anything is written, as ``write_derivation_edges`` does:
a refused third source must not leave the first two behind as a half-recorded lineage.
"""

import dataclasses
from collections.abc import Sequence

from django.core.exceptions import ValidationError
from kante.types import Info
from pydantic import BaseModel, ConfigDict

from core import enums, models
from core.creation import CreationContext
from core.scoping import get_for_org


class AnnotationSourceInputModel(BaseModel):
    """One shape a new shape was made from, and how."""

    model_config = ConfigDict(extra="forbid")

    annotation: str
    relation: enums.AnnotationRelation
    reason: str | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedSource:
    """A source shape, fetched and checked, with what the link will say about it."""

    source: "models.Annotation"
    relation: str
    reason: str | None


def resolve_sources(info: Info, *, child_kind: str, entries: Sequence[AnnotationSourceInputModel], position: str = "derivedFrom") -> list[ResolvedSource]:
    """Fetch and check the sources one new shape names, in its declared order.

    A source is fetched as every read is (``get_for_org``): a guest names what their
    collaborations show them, and nothing of another organization resolves at all. Whether
    the caller may *draw* is the target collection's check, made by the mutation already.
    """
    if not entries:
        return []

    relations = [entry.relation.value for entry in entries]
    merged = enums.AnnotationRelation.MERGED.value
    if merged in relations and (len(entries) < 2 or any(relation != merged for relation in relations)):
        raise ValueError(
            f"`{position}` names a MERGED source, so the shape was joined from several: name every source it was joined from, each as MERGED -- at least two. "
            "A shape made from one source was REFINED, CONVERTED or SPLIT from it"
        )

    seen: set[str] = set()
    resolved: list[ResolvedSource] = []
    for entry in entries:
        try:
            source = get_for_org(models.Annotation, info, id=entry.annotation)
        except (models.Annotation.DoesNotExist, ValidationError) as error:
            # A malformed UUID is as absent as an unknown one: either way there is no such shape here.
            raise ValueError(f"`{position}` names annotation {entry.annotation}, which does not exist or is not visible to you") from error
        if str(source.pk) in seen:
            raise ValueError(f"`{position}` names annotation {source.pk} more than once. One entry per source: its relation already says how the shape came from it")
        seen.add(str(source.pk))

        relation = entry.relation.value
        if relation == enums.AnnotationRelation.REFINED.value and source.kind != child_kind:
            raise ValueError(
                f"A REFINED shape is the same kind as its source, but annotation {source.pk} is a {source.kind} and the new shape a {child_kind}. "
                "A shape drawn as another kind was CONVERTED"
            )
        if relation == enums.AnnotationRelation.CONVERTED.value and source.kind == child_kind:
            raise ValueError(
                f"A CONVERTED shape is drawn as another kind than its source, but both are {child_kind}s. A shape of the same kind with better geometry was REFINED"
            )
        resolved.append(ResolvedSource(source=source, relation=relation, reason=entry.reason))
    return resolved


def build_links(child: "models.Annotation", sources: Sequence[ResolvedSource], ctx: CreationContext) -> list["models.AnnotationDerivation"]:
    """The unsaved link rows for one child, in its declared order."""
    return [
        models.AnnotationDerivation(
            child=child,
            source=resolved.source,
            relation=resolved.relation,
            reason=resolved.reason,
            organization=ctx.organization,
            creator=ctx.user,
            **ctx.provenance_kwargs(),
        )
        for resolved in sources
    ]


def write_links(rows: Sequence["models.AnnotationDerivation"]) -> list["models.AnnotationDerivation"]:
    """Insert the links of one create, in order: pk order is the declared order."""
    if not rows:
        return []
    return models.AnnotationDerivation.objects.bulk_create(list(rows))
