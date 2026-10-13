"""A port's ``requires``, asked of the rows in SQL: which lenses, datasets or annotations satisfy these constraints.

The other direction of ``core.descriptors``. A describer answers what one object is; this
answers which objects are something, so a picker for a port typed ``Volume`` offers the
volumes and pages through them without a describer run per row.

A constraint is ``{key, operator, value}``, the shape a rekuest port states it in, and the
operators mean what ``arkitekt_spec.declare.descriptors.holds`` says they mean (``MATCHES`` is
equality). Every constraint must hold.

Where a key is read from:

- **Stored.** What cannot change after the row is made is on ``stored_descriptors`` (lenses,
  array datasets), so equality is JSONB containment, served by the ``jsonb_path_ops`` index,
  and an ordering is a comparison of one member.
- **Live.** ``@mikro/value_kind`` follows the derivation edges and ``@mikro/n_annotations`` the
  shapes drawn, so each is a correlated subquery; one annotation's kind is its column and its
  axis counts are counted from its collection's space.

A key the structure does not declare is refused, never ignored: a filter that silently drops
a constraint offers objects the port will then refuse. ``tests/test_requires_filter.py`` holds
the two directions to each other, and pins that every declared key can be asked.
"""

from collections.abc import Callable, Iterable, Sequence
from enum import Enum
from typing import Any

import strawberry
from django.contrib.contenttypes.models import ContentType
from django.db.models import CharField, Count, F, IntegerField, OuterRef, Q, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce
from strawberry.scalars import JSON

from core import enums, models
from mikro_server.vocabulary import (
    ANNOTATION_KIND,
    ANNOTATION_SHAPE_DESCRIPTORS,
    ARRAY_DESCRIPTORS,
    DESCRIPTORS_BY_KIND,
    GEOMETRY_AXIS_TYPES,
    KEY_BY_AXIS_TYPE,
    N_ANNOTATIONS,
    VALUE_KIND,
)


@strawberry.enum(description="How a descriptor constraint compares the object's value with the stated one. The operators a rekuest port's `requires` and `provides` are written in, with the same meaning.")
class DescriptorOperator(str, Enum):
    """The comparison of one constraint."""

    EQUALS = "EQUALS"
    MATCHES = "MATCHES"
    NOT_EQUALS = "NOT_EQUALS"
    GTE = "GTE"
    LTE = "LTE"
    EXISTS = "EXISTS"
    CONTAINS = "CONTAINS"
    IN = "IN"
    NOT_IN = "NOT_IN"


@strawberry.input(
    description=(
        "One constraint on an object's descriptors, in the shape a rekuest port states its `requires` in -- so a picker hands a port's constraints on unchanged. "
        "`key` is one of the keys the structure declares (e.g. `@mikro/n_space_axes`); a key it does not declare is refused."
    )
)
class DescriptorConstraintInput:
    """One ``{key, operator, value}`` constraint."""

    key: str = strawberry.field(description="The descriptor key, e.g. `@mikro/n_space_axes`")
    operator: DescriptorOperator = strawberry.field(description="How the object's value is compared with `value`. MATCHES is equality, as EQUALS is")
    value: JSON | None = strawberry.field(default=None, description="The value compared against: a number for GTE and LTE, a list for IN and NOT_IN, true or false for EXISTS, the member looked for with CONTAINS")


Constraint = DescriptorConstraintInput
#: A translator turns one constraint into the condition on its rows; it may annotate the queryset.
Translator = Callable[[QuerySet, str, Constraint], tuple[QuerySet, Q]]

_EQUALITY = (DescriptorOperator.EQUALS, DescriptorOperator.MATCHES)
_MEMBERSHIP = (DescriptorOperator.IN, DescriptorOperator.NOT_IN)


def _annotate_once(queryset: QuerySet, alias: str, expression: Any) -> QuerySet:  # noqa: ANN401 - any Django expression
    """Annotate unless the alias is taken: AND/OR branches of one filter share a queryset (see ``core.filters._annotate_once``)."""
    if alias in queryset.query.annotations:
        return queryset
    return queryset.annotate(**{alias: expression})


def _alias(prefix: str, name: str) -> str:
    return f"_{prefix.replace('__', '_')}{name}"


def _listed(constraint: Constraint) -> list:
    if not isinstance(constraint.value, list):
        raise ValueError(f"{constraint.operator.value} on {constraint.key} takes a list of values, got {constraint.value!r}.")
    return constraint.value


def _number(constraint: Constraint) -> int | float:
    value = constraint.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{constraint.operator.value} on {constraint.key} takes a number, got {value!r}.")
    return value


def _any(conditions: Iterable[Q]) -> Q:
    """The disjunction of these, and nothing when there are none."""
    combined: Q | None = None
    for condition in conditions:
        combined = condition if combined is None else combined | condition
    return combined if combined is not None else Q(pk__in=[])


# --- a key on the stored column -------------------------------------------------


def stored_q(column: str, constraint: Constraint) -> Q:
    """One constraint against a ``stored_descriptors`` column.

    Containment for everything that is an equality, so the GIN index serves it. A negation
    also asks that the key is there: a row whose descriptors were never stated is not a row
    that differs.
    """
    key, operator, value = constraint.key, constraint.operator, constraint.value
    present = Q(**{f"{column}__has_key": key})
    if operator in _EQUALITY:
        return Q(**{f"{column}__contains": {key: value}})
    if operator is DescriptorOperator.NOT_EQUALS:
        return present & ~Q(**{f"{column}__contains": {key: value}})
    if operator is DescriptorOperator.IN:
        return _any(Q(**{f"{column}__contains": {key: member}}) for member in _listed(constraint))
    if operator is DescriptorOperator.NOT_IN:
        return present & ~_any(Q(**{f"{column}__contains": {key: member}}) for member in _listed(constraint))
    if operator is DescriptorOperator.CONTAINS:
        return Q(**{f"{column}__contains": {key: [value]}})
    if operator is DescriptorOperator.EXISTS:
        if value not in (None, True, False):
            raise ValueError(f"EXISTS on {key} takes true or false, got {value!r}.")
        return ~present if value is False else present
    # jsonb compares two numbers as numbers, so the member is compared as it is stored.
    lookup = "gte" if operator is DescriptorOperator.GTE else "lte"
    return present & Q(**{f"{column}__{key}__{lookup}": _number(constraint)})


def _stored(queryset: QuerySet, prefix: str, constraint: Constraint) -> tuple[QuerySet, Q]:
    return queryset, stored_q(f"{prefix}stored_descriptors", constraint)


# --- a key compared as a plain value (a column, an annotation) --------------------


def _scalar_q(lookup: str, constraint: Constraint) -> Q:
    """One constraint against an annotation that holds the descriptor's value itself."""
    operator = constraint.operator
    if operator in _EQUALITY:
        return Q(**{lookup: constraint.value})
    if operator is DescriptorOperator.NOT_EQUALS:
        return ~Q(**{lookup: constraint.value})
    if operator is DescriptorOperator.IN:
        return Q(**{f"{lookup}__in": _listed(constraint)})
    if operator is DescriptorOperator.NOT_IN:
        return ~Q(**{f"{lookup}__in": _listed(constraint)})
    if operator is DescriptorOperator.GTE:
        return Q(**{f"{lookup}__gte": _number(constraint)})
    if operator is DescriptorOperator.LTE:
        return Q(**{f"{lookup}__lte": _number(constraint)})
    if operator is DescriptorOperator.EXISTS:
        # The value is always there: every row of the structure states this key.
        return Q(pk__in=[]) if constraint.value is False else Q()
    raise ValueError(f"{operator.value} is not an operator {constraint.key} can be asked with: it holds one value, not a list.")


# --- @mikro/value_kind ------------------------------------------------------------


def _derivation_edges() -> QuerySet:
    """Every derivation edge leaving a dataset's own grid: ``graph_logic.derivation_edges`` as a query.

    The same predicate ``core.filters._derived_dataset_ids`` states, and it has to agree with
    ``graph_logic.is_derivation_edge``: not a FIELD, landing in a container, and in another
    one than the dataset it leaves.
    """
    source_dataset = Coalesce("output__datasets__id", "output__lenses__arraylens__dataset_id", "output__data_arrays__dataset_id")
    source_collection = Coalesce(
        "output__table_datasets__id",
        "output__sparse_datasets__id",
        "output__mesh_collections__id",
        "output__network_collections__id",
        "output__annotation_collections__id",
    )
    return (
        models.Transformation.objects.filter(input__datasets__isnull=False)
        .exclude(kind=enums.TransformKindChoices.FIELD.value)
        .annotate(_source_dataset=source_dataset, _source_collection=source_collection)
        .filter((Q(_source_dataset__isnull=False) & ~Q(_source_dataset=F("input__datasets__id"))) | Q(_source_collection__isnull=False))
    )


def _value_kind_conditions(queryset: QuerySet, prefix: str, dataset: str) -> tuple[QuerySet, dict[str, Q]]:
    """The condition under which a row is each value kind, in ``core.descriptors.array_value_kind``'s order.

    ``dataset`` is the path from the row to its array dataset ("" when the row is one). The
    primary derivation is the first by pk, kind-blind, as ``graph_logic.categorized_dataset_ids``
    picks it; its value relation is annotated once and each kind is a condition on it and on
    the dataset's stored spec.
    """
    alias = _alias(prefix, "primary_value_relation")
    primary = _derivation_edges().filter(input__datasets__id=OuterRef(f"{prefix}{dataset}pk")).order_by("pk").values("value_relation")[:1]
    # Never null: a dataset with no derivation has no relation, and a negated test of a null is not true.
    queryset = _annotate_once(queryset, alias, Coalesce(Subquery(primary, output_field=CharField()), Value("")))
    spec = f"{prefix}{dataset}stored_spec__contains"
    categorical = Q(**{alias: enums.ValueRelationChoices.CATEGORIZED.value})
    vector = ~categorical & Q(**{spec: [enums.ArrayDatasetSpec.DISPLACEMENT_FIELD.value]})
    coordinates = ~categorical & ~Q(**{spec: [enums.ArrayDatasetSpec.DISPLACEMENT_FIELD.value]}) & Q(**{spec: [enums.ArrayDatasetSpec.COORDINATE_FIELD.value]})
    intensity = ~categorical & ~Q(**{spec: [enums.ArrayDatasetSpec.DISPLACEMENT_FIELD.value]}) & ~Q(**{spec: [enums.ArrayDatasetSpec.COORDINATE_FIELD.value]})
    return queryset, {"categorical": categorical, "vector": vector, "coordinates": coordinates, "intensity": intensity}


def _value_kind(dataset: str) -> Translator:
    def translate(queryset: QuerySet, prefix: str, constraint: Constraint) -> tuple[QuerySet, Q]:
        queryset, kinds = _value_kind_conditions(queryset, prefix, dataset)
        operator = constraint.operator
        if operator in _EQUALITY or operator is DescriptorOperator.NOT_EQUALS:
            wanted = [constraint.value]
        elif operator in _MEMBERSHIP:
            wanted = _listed(constraint)
        elif operator is DescriptorOperator.EXISTS:
            return queryset, Q(pk__in=[]) if constraint.value is False else Q()
        else:
            raise ValueError(f"{operator.value} is not an operator {VALUE_KIND} can be asked with: it is one of {sorted(kinds)}.")
        matched = _any(kinds[kind] for kind in wanted if kind in kinds)
        return queryset, ~matched if operator in (DescriptorOperator.NOT_EQUALS, DescriptorOperator.NOT_IN) else matched

    return translate


# --- annotations: a collection's count, one shape's kind and axes -----------------


def _n_annotations(queryset: QuerySet, prefix: str, constraint: Constraint) -> tuple[QuerySet, Q]:
    """How many shapes a *whole* annotation lens holds, counted in SQL.

    A windowed lens counts the shapes whose box lies in its windows, which is geometry done in
    Python (``core.descriptors.annotations_in_windows``); it is left out of the answer rather
    than counted wrongly.
    """
    alias = _alias(prefix, "n_annotations")
    counted = (
        models.Annotation.objects.filter(collection_id=OuterRef(f"{prefix}annotationlens__annotation_collection_id"))
        .order_by()
        .values("collection_id")
        .annotate(_n=Count("pk"))
        .values("_n")
    )
    queryset = _annotate_once(queryset, alias, Coalesce(Subquery(counted, output_field=IntegerField()), 0))
    return queryset, Q(**{f"{prefix}annotationlens__windows": []}) & _scalar_q(alias, constraint)


_ANNOTATION_KINDS = {member.name: member for member in enums.AnnotationKindChoices}


def _annotation_kind(queryset: QuerySet, prefix: str, constraint: Constraint) -> tuple[QuerySet, Q]:
    """One shape's kind, read off its column. The descriptor names a kind as the enum does (``PATH``); a name that is no kind is a kind no row has."""
    operator = constraint.operator
    if operator is DescriptorOperator.EXISTS:
        return queryset, Q(pk__in=[]) if constraint.value is False else Q()
    if operator in _EQUALITY or operator is DescriptorOperator.NOT_EQUALS:
        named = [constraint.value]
    elif operator in _MEMBERSHIP:
        named = _listed(constraint)
    else:
        raise ValueError(f"{operator.value} is not an operator {ANNOTATION_KIND} can be asked with: it is one of {sorted(_ANNOTATION_KINDS)}.")
    kinds = [_ANNOTATION_KINDS[str(name).upper()] for name in named if str(name).upper() in _ANNOTATION_KINDS]
    matched = Q(**{f"{prefix}kind__in": kinds})
    return queryset, ~matched if operator in (DescriptorOperator.NOT_EQUALS, DescriptorOperator.NOT_IN) else matched


def _annotation_axis_count(axis_type: str) -> Translator:
    def translate(queryset: QuerySet, prefix: str, constraint: Constraint) -> tuple[QuerySet, Q]:
        # A subquery, not a join: another filter's join would multiply a joined count out.
        alias = _alias(prefix, f"n_{axis_type.lower()}_axes")
        counted = (
            models.Axis.objects.filter(coordinate_system_id=OuterRef(f"{prefix}collection__coordinate_system_id"), type=axis_type)
            .order_by()
            .values("coordinate_system_id")
            .annotate(_n=Count("pk"))
            .values("_n")
        )
        queryset = _annotate_once(queryset, alias, Coalesce(Subquery(counted, output_field=IntegerField()), 0))
        return queryset, _scalar_q(alias, constraint)

    return translate


# --- the registry -----------------------------------------------------------------


def _only(kind: "type[models.Lens]", translator: Translator) -> Translator:
    """A translator for a key only one lens kind states: every other kind lacks the key, so it cannot hold."""

    def translate(queryset: QuerySet, prefix: str, constraint: Constraint) -> tuple[QuerySet, Q]:
        queryset, condition = translator(queryset, prefix, constraint)
        return queryset, Q(**{f"{prefix}polymorphic_ctype": ContentType.objects.get_for_model(kind)}) & condition

    return translate


def _lens_translators() -> dict[str, Translator]:
    translators: dict[str, Translator] = {descriptor.key: _stored for descriptors in DESCRIPTORS_BY_KIND.values() for descriptor in descriptors}
    translators[VALUE_KIND] = _only(models.ArrayLens, _value_kind("arraylens__dataset__"))
    translators[N_ANNOTATIONS] = _only(models.AnnotationLens, _n_annotations)
    return translators


def _dataset_translators() -> dict[str, Translator]:
    translators: dict[str, Translator] = {descriptor.key: _stored for descriptor in ARRAY_DESCRIPTORS}
    translators[VALUE_KIND] = _value_kind("")
    return translators


def _annotation_translators() -> dict[str, Translator]:
    translators: dict[str, Translator] = {KEY_BY_AXIS_TYPE[axis_type]: _annotation_axis_count(axis_type) for axis_type in GEOMETRY_AXIS_TYPES}
    translators[ANNOTATION_KIND] = _annotation_kind
    return translators


#: What can be asked of each filtered model, by descriptor key.
TRANSLATORS: dict[type, dict[str, Translator]] = {
    models.Lens: _lens_translators(),
    models.ArrayDataset: _dataset_translators(),
    models.Annotation: _annotation_translators(),
}
#: The keys each of them declares, which is what the registry is held to.
DECLARED: dict[type, set[str]] = {
    models.Lens: {descriptor.key for descriptors in DESCRIPTORS_BY_KIND.values() for descriptor in descriptors},
    models.ArrayDataset: {descriptor.key for descriptor in ARRAY_DESCRIPTORS},
    models.Annotation: {descriptor.key for descriptor in ANNOTATION_SHAPE_DESCRIPTORS},
}


def requires(model: type, queryset: QuerySet, constraints: Sequence[Constraint], prefix: str = "") -> tuple[QuerySet, Q]:
    """The rows of ``model`` satisfying every one of these constraints. No constraints ask nothing."""
    translators = TRANSLATORS[model]
    condition = Q()
    for constraint in constraints:
        translator = translators.get(constraint.key)
        if translator is None:
            raise ValueError(f"{constraint.key!r} is not a descriptor of {model.__name__}: it declares {sorted(translators)}.")
        queryset, matched = translator(queryset, prefix, constraint)
        condition &= matched
    return queryset, condition
