"""Registries and lookup tables that must follow the models and the schema they describe."""

import os
import re


def test_minimum_vertex_table_is_keyed_on_the_live_annotation_kinds() -> None:
    """Every key must be a kind an annotation can actually be drawn as.

    It carried six that no longer were -- `spectral_rectangle`, `temporal_cube` and four
    siblings -- left behind when `AnnotationKindChoices` replaced the ROI vocabulary that had
    spelled a box six ways. No lookup could ever reach them, so nothing failed and nothing said
    so. The table and the enum are two halves of one fact; this is the half that was missing.
    """
    from core.enums import AnnotationKindChoices
    from core.inputs.validators import _MINIMUM_VERTICES

    live = {member.value for member in AnnotationKindChoices}
    unreachable = sorted(set(_MINIMUM_VERTICES) - live)
    assert not unreachable, f"_MINIMUM_VERTICES keys no annotation kind can reach: {unreachable}"


def test_every_model_that_owns_a_coordinate_system_is_a_registered_container() -> None:
    """The container registry is a consequence of the models, not a list to remember.

    A model with a ``coordinate_system`` FK is, by definition, somewhere data lives. If it is
    missing from ``CONTAINERS`` its space is read two incompatible ways at once: `derivedFrom`
    and the attribute-plan walk treat it as inhabited, while ``_UNINHABITED`` -- which is derived
    from the registry -- does not know to ask about it, so the same space keys as an
    **uninhabited reference frame** and is excluded from the `fact_paths` frontier. It also drops
    out of `CoordinateSystem.residents` and `LineageGraph.nodes`, silently.

    That is not hypothetical twice over. ``SparseDataset`` sat in exactly this state until the
    registry replaced six hand-written lists, and the comment on it in `CONTAINERS` says so.
    ``NetworkCollection`` then arrived and reproduced it, because adding a model and adding a
    *type* both look complete on their own. Deriving the expectation from the FK is what makes
    the next one impossible rather than merely unlikely.
    """
    import django

    django.setup()
    from django.apps import apps

    from core.logic.graph import CONTAINERS

    # Models that carry the FK and are deliberately not containers, each with the reason.
    # Explicit rather than heuristic, in the same spirit as `DATALAYER_MODULES` above: the
    # friction of adding a name here is the point, because it is the moment to ask whether the
    # thing really is data living in a space or part of the space's own definition.
    not_containers = {
        # An axis is a component of a coordinate system, not something residing in one. It is
        # on the other side of the relationship from everything in `CONTAINERS`.
        "Axis",
        # The polymorphic base of the six lens kinds: every row of it is a row of one of them,
        # and it is the kinds that are registered, each keyed to the container it selects over.
        "Lens",
    }

    registered = {container.model for container in CONTAINERS}
    owners = {
        model
        for model in apps.get_app_config("core").get_models()
        # The historical twins carry the same FK and are not containers: they are rows about
        # rows, and nothing lives in them.
        if not model.__name__.startswith("Historical")
        and any(
            field.name == "coordinate_system" and field.is_relation
            for field in model._meta.get_fields()
        )
    }

    missing = sorted(model.__name__ for model in owners - registered if model.__name__ not in not_containers)
    assert not missing, (
        f"{', '.join(missing)} own a coordinate system but are not in `CONTAINERS`, so their "
        f"spaces will key as uninhabited worlds and vanish from `residents` and `LineageGraph`."
    )


def test_every_registered_container_is_a_member_of_the_resident_union() -> None:
    """A container the union does not name is a resident nothing can return.

    The second half of the same gap, and it fails differently: the walk finds the container, the
    resolver tries to return it, and strawberry has no type for it. Checked against the SDL
    rather than the Python union so that a member which is declared but never registered in the
    schema -- the failure mode the `layer_types` list exists for -- is caught too.
    """

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mikro_server.settings_test")
    django.setup()

    from core.logic.graph import CONTAINERS
    from mikro_server.schema import schema

    match = re.search(r"union Resident = ([^\n]+)", schema.as_str())
    assert match, "the SDL declares no `Resident` union at all"
    members = {name.strip() for name in match.group(1).split("|")}

    missing = sorted(
        container.model.__name__
        for container in CONTAINERS
        if container.model.__name__ not in members
    )
    assert not missing, f"{', '.join(missing)} are containers but not members of `Resident`: {sorted(members)}"


def test_every_layer_kind_is_in_the_refusal_vocabulary() -> None:
    """`_KIND_VOCABULARY` says a refusal must name the mutation to use instead.

    Its own comment promises that "adding a kind cannot leave a guard silently listing eight of
    nine" -- but nothing enforced that, and adding ``NETWORK`` did exactly that. The cost is not
    a crash: `assert_kind` reads the *actual* kind through `.get(...)` with a fallback, so a
    layer of a missing kind is refused with "different render settings" instead of the name of
    the mutation that wants it. That is a worse error message arriving at the moment someone is
    already confused, which is the moment the message exists for.
    """
    import django

    django.setup()

    from core import enums
    from core.mutations.layer import _KIND_VOCABULARY

    missing = sorted(kind.value for kind in enums.LayerKind if kind.value not in _KIND_VOCABULARY)
    assert not missing, f"{', '.join(missing)} are layer kinds with no entry in `_KIND_VOCABULARY`"


def test_every_picker_column_is_guarded() -> None:
    """The delete guards cover every JSON column a picker is stored in.

    `core.logic.pickers` promised from the start that "a new picker on a new layer kind must
    be added here, and the test that walks every layer kind is what will say so" -- and no
    such test existed. The point pickers shipped into that gap: a table a point layer coloured
    by deleted cleanly and stranded the entry as a join nothing could execute, surfacing at
    render time to whoever opened the scene next. Derived from the model rather than restating
    the list, so the next layer kind's columns fail this the moment they exist.
    """
    import django

    django.setup()

    from django.db import models as django_models

    from core import models
    from core.logic.pickers import _LABEL_PICKER_KEYS, _PICKER_COLUMNS
    from core.render.layer.label import LabelRenderModel

    stored = sorted(
        field.name
        for field in models.Layer._meta.get_fields()
        if isinstance(field, django_models.JSONField)
        and (field.name.endswith("_color_bys") or field.name.endswith("_filter_bys"))
        # `active_filter_bys` shares the suffix but stores indices into a picker, not
        # entries naming a source -- there is nothing in it a delete could strand.
        and not field.name.startswith("active_")
    )
    unguarded = [name for name in stored if name not in _PICKER_COLUMNS]
    assert not unguarded, (
        f"{', '.join(unguarded)} store pickers the delete guards never look at: a table or "
        f"sparse dataset an entry there names deletes cleanly and strands the entry. Add them "
        f"to `core.logic.pickers._PICKER_COLUMNS`."
    )
    stale = [name for name in _PICKER_COLUMNS if name not in stored]
    assert not stale, f"{', '.join(stale)} are guarded columns the Layer model does not have -- a rename left the guard behind"

    # The label pickers live inside the `label_render` blob rather than in columns of their
    # own, so their keys are held to the render model instead.
    missing_keys = [key for key in _LABEL_PICKER_KEYS if key not in LabelRenderModel.model_fields]
    assert not missing_keys, f"{', '.join(missing_keys)} are guarded label_render keys the render model does not carry"


def test_every_layer_kind_has_exactly_one_registered_concrete_type() -> None:
    """A `Layer` subtype not in `layer_types` is dropped from the SDL, silently.

    `core/types/layers.py` says it outright: a subtype reachable only through the interface is
    not auto-discovered, so leaving it out means "no error at import and no error at query time
    -- the field simply is not there". A client asking for `... on NetworkLayer { ... }` gets a
    validation error against a schema that looks otherwise complete.

    Asked **behaviourally** rather than by name: each registered type is offered a stand-in
    carrying each kind and asked `is_type_of`, which is exactly what the resolver does. A
    name-shaped check (`Rgb` vs `RGB`) would encode a convention nothing enforces, and would
    pass for a type whose `is_type_of` compares against the wrong kind -- which is the mistake
    that actually happens when a concrete type is written by copying its neighbour.
    """

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mikro_server.settings_test")
    django.setup()

    from core import enums, models
    from core.types.layers import layer_types
    from mikro_server.schema import schema

    sdl = schema.as_str()
    claims: dict[str, list[str]] = {kind.value: [] for kind in enums.LayerKind}
    for layer_type in layer_types:
        name = layer_type.__name__
        assert f"type {name} implements Layer" in sdl, f"{name} is registered but absent from the SDL"
        for kind in enums.LayerKind:
            if layer_type.is_type_of(models.Layer(kind=kind.value), None):
                claims[kind.value].append(name)

    unclaimed = sorted(kind for kind, names in claims.items() if not names)
    assert not unclaimed, (
        f"{', '.join(unclaimed)} are layer kinds no registered type resolves to. A layer of that "
        f"kind is stored fine and then cannot be read back through the interface."
    )

    ambiguous = {kind: names for kind, names in claims.items() if len(names) > 1}
    assert not ambiguous, f"more than one concrete type claims the same kind: {ambiguous}"


def _query_fields_reading_rows():
    """Every Query field strawberry_django resolves itself, as (name, django type, model).

    Fields with their own resolver are excluded: they scope explicitly, through
    ``core.scoping``. What remains is every field whose only line of defence is the type's
    ``get_queryset``.
    """
    from mikro_server.schema import Query

    for field in Query.__strawberry_definition__.fields:
        if getattr(field, "base_resolver", None) is not None:
            continue
        model = getattr(field, "django_model", None)
        if model is None:
            continue
        yield field.python_name, field.django_type, model


def test_every_query_field_is_scoped_to_the_organization() -> None:
    """A bare `x: list[T] = field()` or `x: T = field()` reads through T's `get_queryset`, and
    nothing else in the stack adds an organization. Twelve types once had none, so every
    organization's scenes, datasets and folders were one query away. Each such field must
    narrow to the request's organization -- checked on the SQL, so a `get_queryset` that only
    selects relations (as Layer's once did) does not count.
    """
    from types import SimpleNamespace

    from authentikate.models import Organization

    from core.scoping import organization_path

    sentinel = 987654321
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(organization=Organization(pk=sentinel))))

    unscoped = []
    for name, django_type, model in _query_fields_reading_rows():
        get_queryset = getattr(django_type, "get_queryset", None)
        if get_queryset is None:
            unscoped.append(f"{name} ({django_type.__name__} has no get_queryset)")
            continue
        sql = str(get_queryset(model.objects.all(), info).query)
        if organization_path(model) is None or str(sentinel) not in sql:
            unscoped.append(f"{name} ({django_type.__name__}.get_queryset does not filter by organization)")

    assert not unscoped, "Query fields readable across organizations:\n  " + "\n  ".join(unscoped)


def test_no_string_prefetch_hint_crosses_a_scoped_relation() -> None:
    """A string prefetch hint must not start with a relation the optimizer prefetches itself.

    For a relation whose target type has a ``get_queryset`` (every ``OrgScoped`` type), the
    optimizer builds ``Prefetch(<relation>, queryset=<scoped>)``. A hint on a sibling field
    that walks the same relation as a plain string -- ``"input__axes"`` next to
    ``input: CoordinateSystem`` -- makes Django see one lookup with two querysets, and the
    whole field fails at request time with "'input' lookup was already seen with a different
    queryset" (every pyramid level's ``toParent.transformations``, once ``CoordinateSystem``
    became scoped). Nothing fails at import or in a query that happens to select only one of
    the two, so this is checked statically: such a relation must be a resolved field with
    ``select_related`` hints (see ``Transformation.input``), or the hint must go.
    """
    from strawberry_django.fields.field import StrawberryDjangoField
    from strawberry_django.optimizer import get_possible_type_definitions
    from strawberry_django.utils.typing import get_django_definition

    from mikro_server.schema import schema

    clashes = set()
    for graphql_type in schema._schema.type_map.values():
        definition = (getattr(graphql_type, "extensions", None) or {}).get("strawberry-definition")
        if definition is None or not hasattr(definition, "fields") or not get_django_definition(definition.origin):
            continue
        # Relations left to the optimizer: plain Django fields, no resolver of their own.
        optimized = {field.django_name: field for field in definition.fields if isinstance(field, StrawberryDjangoField) and field.base_resolver is None}
        for field in definition.fields:
            store = getattr(field, "store", None)
            for hint in store.prefetch_related if store else ():
                if not isinstance(hint, str) or "__" not in hint:
                    continue
                relation = optimized.get(hint.split("__", 1)[0])
                if relation is None:
                    continue
                targets = [target.origin for target in get_possible_type_definitions(relation.type)]
                if any(hasattr(target, "get_queryset") for target in targets):
                    clashes.add(f"{definition.origin.__name__}.{field.python_name} hints {hint!r} across `{relation.python_name}`, which the optimizer prefetches scoped")

    assert not clashes, "String prefetch hints that clash with a scoped relation's Prefetch:\n  " + "\n  ".join(sorted(clashes))
