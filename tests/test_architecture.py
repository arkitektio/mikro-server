"""Guards for the dependency rules between the apps.

The rule (documented in ``core/models/__init__.py``): core may depend on
datalayer, but datalayer is the storage backend and must never import core.
"""

import ast
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

DATALAYER_DIR = Path(__file__).parent.parent / "datalayer"

# Explicit list — a new datalayer module should be added here deliberately.
DATALAYER_MODULES = [
    "admin.py",
    "apps.py",
    "base_models.py",
    "datalayer.py",
    "duck.py",
    "fields.py",
    "inputs.py",
    "fabriks.py",
    "models.py",
    "mutations/bigfile.py",
    "mutations/media.py",
    "mutations/fabriks.py",
    "mutations/parquet.py",
    "mutations/konnektion.py",
    "mutations/sparse.py",
    "mutations/zarr.py",
    "konnektion.py",
    "scalars.py",
    "sporadik.py",
    "types.py",
]


def _imported_top_level_packages(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    packages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            packages.add(node.module.split(".")[0])
    return packages


def test_the_plan_sql_module_imports_only_the_stdlib() -> None:
    """``core/logic/plan_sql.py`` is copied into the client unchanged, so it may import nothing of ours.

    The builder used to be the plan's own ``sql`` field. Dropping the field is only honest if
    every consumer can rebuild the string from the structured step, and "every consumer"
    includes a Python client that does not carry Django, strawberry or this package.
    """
    imported = _imported_top_level_packages(Path(__file__).resolve().parent.parent / "core" / "logic" / "plan_sql.py")
    foreign = imported - sys.stdlib_module_names
    assert not foreign, f"plan_sql imports {sorted(foreign)}, which the client cannot carry"


def test_datalayer_module_list_is_current() -> None:
    """Fail when a datalayer module is added without registering it above."""
    on_disk = sorted(
        str(p.relative_to(DATALAYER_DIR))
        for p in DATALAYER_DIR.rglob("*.py")
        if p.name != "__init__.py" and "migrations" not in p.parts and "__pycache__" not in p.parts
    )
    assert on_disk == sorted(DATALAYER_MODULES)


@pytest.mark.parametrize("module", DATALAYER_MODULES)
def test_datalayer_does_not_import_core(module: str) -> None:
    """datalayer is the storage backend; it must stay ignorant of the domain apps."""
    imported = _imported_top_level_packages(DATALAYER_DIR / module)
    assert "core" not in imported, f"datalayer/{module} imports core"


# ==========================================================================================
# Guards against the drift classes a coordinate-system review found by hand.
#
# Each of these would have caught real findings at the commit that introduced them, rather
# than years later by reading: a docstring naming a function that was never written, a lookup
# table keyed on an enum that stopped backing its field, and a model edited without its
# migration.
# ==========================================================================================

CORE_DIR = Path(__file__).parent.parent / "core"

#: Words that look like dotted identifiers inside prose and are not references to resolve.
#: Explicit, in the same spirit as `DATALAYER_MODULES` above -- the friction of adding a name
#: here is the point, because it is the moment to ask whether the prose meant a real symbol.
_PROSE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "e.g",
        "i.e",
        "a.u",
        "etc",
        "vs",
        "np.float32",
        "fabriks.json",
    }
)

#: Sphinx cross-reference roles whose target must name something that exists.
_XREF = re.compile(r":(?:func|meth|attr|class|mod|data|exc):`~?([A-Za-z_][\w.]*)`")


def _core_symbols() -> tuple[set[str], dict[str, set[str]]]:
    """Every name `core` defines, and every class' member names, read statically.

    Static rather than by import because a reference may legitimately name something in a module
    this test never imports, and because importing all of `core` to answer "does this name
    exist" would make the guard depend on import side effects.
    """
    names: set[str] = set()
    members: dict[str, set[str]] = {}
    for path in CORE_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or "migrations" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a broken file is another test's problem
            continue
        names.add(path.stem)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            if isinstance(node, ast.ClassDef):
                owned = members.setdefault(node.name, set())
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        owned.add(child.name)
                    elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                        owned.add(child.target.id)
                    elif isinstance(child, ast.Assign):
                        owned.update(t.id for t in child.targets if isinstance(t, ast.Name))
    return names, members


def _reference_resolves(dotted: str, names: set[str], members: dict[str, set[str]]) -> bool:
    """Whether a cited name plausibly exists.

    Three shapes appear in this codebase and all three are legitimate:
    a fully qualified path (``core.logic.graph.is_traversable``), a partial one
    (``coords.to_matrix``), and a bare name relying on context (``placement_path``). So a
    reference resolves when it imports outright, or when its owner is a known class that has
    the member, or when its final component is a name `core` defines somewhere.

    Deliberately permissive about *where* a name lives and strict about whether it exists at
    all -- which is the axis the findings this guards against sat on. `Transformation.registration`
    fails because no class named `Transformation` has that member; `best_path` fails because
    nothing anywhere defines it.
    """
    if _imports(dotted):
        return True
    parts = dotted.split(".")
    if len(parts) >= 2 and parts[-2] in members:
        return parts[-1] in members[parts[-2]]
    return parts[-1] in names


def _imports(dotted: str) -> bool:
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:split]))
        except Exception:
            continue
        target = module
        for attribute in parts[split:]:
            target = getattr(target, attribute, None)
            if target is None:
                return False
        return True
    return False


def test_sphinx_cross_references_in_core_resolve() -> None:
    """A `:func:`/`:attr:` naming a symbol that does not exist is a docstring describing other code.

    This is the single highest-yield guard in the suite. The coordinate review found six live
    instances by hand -- a `best_path` tie-break that was never written but was cited three times
    as the arbiter of rival registrations, an `intrinsic_of` field removed with the ownership FKs
    and still named in five comments, a `Layer.registration` column that RFC-8 deleted, and a
    `core.logic.roi` module that has never existed. Every one of them was load-bearing prose in a
    codebase where the docstrings *are* the design record, and every one would have failed here.

    Deliberately narrow: only Sphinx roles, which are unambiguous claims that a symbol exists.
    Backticked prose is not checked, because ``a.u.`` and ``fabriks.json`` are not references and
    an allowlist long enough to cover them would stop being read.
    """
    names, members = _core_symbols()
    unresolved: list[str] = []
    for path in sorted(CORE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or "migrations" in path.parts:
            continue
        for match in _XREF.finditer(path.read_text()):
            dotted = match.group(1)
            if dotted in _PROSE_ALLOWLIST:
                continue
            if not _reference_resolves(dotted, names, members):
                unresolved.append(f"{path.relative_to(CORE_DIR.parent)}: {match.group(0)}")
    assert not unresolved, "docstrings name symbols that do not exist:\n  " + "\n  ".join(unresolved)


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


def test_the_models_and_the_migrations_agree() -> None:
    """A model edited without its migration is invisible to the rest of this suite.

    `mikro_server.settings_test` sets `MIGRATION_MODULES = DisableMigrations()`, so the test
    database is built with `run-syncdb` straight from the models and **no migration is ever
    executed here**. That makes model/migration drift completely undetectable by every other
    test: you can add a field, forget the migration, and stay green all the way to a deploy that
    fails on `migrate`. So this one runs the check against the real settings, in a subprocess,
    because the drift it looks for is one the test settings are designed not to see.
    """
    result = subprocess.run(
        [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"],
        cwd=CORE_DIR.parent,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "mikro_server.settings"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"models have changes with no migration:\n{result.stdout}\n{result.stderr}"


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
    import os
    import re

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


def test_the_bootstrap_names_every_collection_before_falling_back_to_the_array() -> None:
    """A collection missing from that guard draws the image it was derived from.

    The subtlest of this family, and the only one whose symptom is a wrong picture rather than a
    missing one. `_materialize_layers` asks the collections first and the array case last,
    because `dataset_behind` follows a derivation edge *backwards*: for a collection's own space
    that edge leads to the volume it was extracted from. So a collection kind that has a branch
    but is absent from the final guard falls through it and bootstraps the **volume** wherever
    that collection was registered -- past its own `include_*` policy flag, and looking for all
    the world like a working scene.
    """
    import inspect

    import django

    django.setup()

    from core.logic import scene as scene_logic
    from core.logic.graph import COLLECTION_CONTAINERS

    source = inspect.getsource(scene_logic._materialize_layers)
    guard = source[source.index("dataset = graph_logic.dataset_behind(source)") :]

    missing = [
        container.related_name
        for container in COLLECTION_CONTAINERS
        if f"source.{container.related_name}.exists()" not in guard
    ]
    assert not missing, (
        f"{', '.join(missing)} are collection containers that `_materialize_layers` does not "
        f"exclude before falling back to the array case, so a system holding one would bootstrap "
        f"the dataset behind it instead."
    )


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
    import os
    from types import SimpleNamespace

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mikro_server.settings_test")
    django.setup()

    from core import enums
    from core.types.layers import layer_types
    from mikro_server.schema import schema

    sdl = schema.as_str()
    claims: dict[str, list[str]] = {kind.value: [] for kind in enums.LayerKind}
    for layer_type in layer_types:
        name = layer_type.__name__
        assert f"type {name} implements Layer" in sdl, f"{name} is registered but absent from the SDL"
        for kind in enums.LayerKind:
            if layer_type.is_type_of(SimpleNamespace(kind=kind.value), None):
                claims[kind.value].append(name)

    unclaimed = sorted(kind for kind, names in claims.items() if not names)
    assert not unclaimed, (
        f"{', '.join(unclaimed)} are layer kinds no registered type resolves to. A layer of that "
        f"kind is stored fine and then cannot be read back through the interface."
    )

    ambiguous = {kind: names for kind, names in claims.items() if len(names) > 1}
    assert not ambiguous, f"more than one concrete type claims the same kind: {ambiguous}"


def test_every_kind_that_draws_a_lens_is_a_lens_backed_kind() -> None:
    """`LENS_BACKED_KINDS` is the sixth hand-written registry, and the only one that had no guard.

    Its own docstring records the failure: the frozenset is read as a *group* to answer which
    layers have a space to be in (`layer_source_system`), which have a pyramid to walk
    (`level_placements`), and which a lens picker can offer -- and when INTENSITY, RGB and
    PHASOR were added to two hand-written `(IMAGE, LABEL)` tuples, the layers came back
    UNREGISTERED rather than erroring. A kind can join the model, the mutations and the SDL
    completely and still be missing here, and nothing raises.

    Both directions, both derived rather than restated. Which kinds *should* be members is
    read off the registered GraphQL types: declaring a `lens` field is the type's own claim
    that the kind sources from a lens, and each type names its kind behaviourally through
    `is_type_of`, exactly as the exactly-one-claimant test above asks it. Membership is then
    checked behaviourally against a consumer: a stand-in layer of each lens-claiming kind is
    handed to `layer_source_system`, which must route it through the lens arm -- the very
    call that answered None and produced UNREGISTERED for the historical omissions.
    """
    import os
    from types import SimpleNamespace

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mikro_server.settings_test")
    django.setup()

    from core import enums
    from core.logic import graph as graph_logic
    from core.types.layers import layer_types

    lens_kinds: set[str] = set()
    for layer_type in layer_types:
        if "lens" not in getattr(layer_type, "__annotations__", {}):
            continue
        for kind in enums.LayerKind:
            if layer_type.is_type_of(SimpleNamespace(kind=kind.value), None):
                lens_kinds.add(kind.value)

    stale = sorted(enums.LENS_BACKED_KINDS - lens_kinds)
    assert not stale, (
        f"{', '.join(stale)} are in LENS_BACKED_KINDS but no registered type carrying a `lens` "
        f"field claims them. Either the kind lost its lens or the set names a kind that never existed."
    )

    space = object()
    for kind in sorted(lens_kinds):
        layer = SimpleNamespace(
            kind=kind,
            lens_id=1,
            lens=SimpleNamespace(coordinate_system=space),
            annotation_collection_id=None,
            mesh_collection_id=None,
            network_collection_id=None,
            table_dataset_id=None,
        )
        assert graph_logic.layer_source_system(layer) is space, (
            f"a {kind} layer draws a lens but `layer_source_system` does not route it through "
            f"the lens arm -- it has no space to be in and comes back UNREGISTERED. Add "
            f"'{kind}' to core.enums.LENS_BACKED_KINDS."
        )
