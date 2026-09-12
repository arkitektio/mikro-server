"""Async ORM seed helpers shared by the filter/ordering test modules.

All helpers create objects in the organization/user of the supplied context
(the identity the static "test" token resolves to, see conftest.py).
"""

from authentikate.models import Membership, User
from kante.context import HttpContext

from core.models import Folder, File, SparseStore
from datalayer.models import sparse_layout_path


async def create_folder(ctx: HttpContext, name: str, **kwargs) -> Folder:
    return await Folder.objects.acreate(
        name=name,
        creator=kwargs.pop("creator", ctx.request.user),
        organization=ctx.request.organization,
        membership=kwargs.pop("membership", ctx.request.membership),
        **kwargs,
    )


async def create_file(ctx: HttpContext, name: str, folder: Folder, **kwargs) -> File:
    return await File.objects.acreate(
        name=name,
        folder=folder,
        creator=kwargs.pop("creator", ctx.request.user),
        organization=ctx.request.organization,
        membership=kwargs.pop("membership", ctx.request.membership),
        **kwargs,
    )


async def create_other_user(ctx: HttpContext) -> User:
    """A second user (sub='2') in the same organization as the context user."""
    user, _ = await User.objects.aget_or_create(
        sub="2", iss="static_issuer", defaults={"username": "static_issuer_2"}
    )
    await Membership.objects.aget_or_create(user=user, organization=ctx.request.organization)
    return user


# --- the coordinate graph ---------------------------------------------------
#
# Seeding an array dataset now means seeding its coordinate systems too: the
# axis names and the pyramid scales all live on the graph, not on columns. The
# intrinsic system is the level-0 pixel grid -- structural axes, no units.
# Physical units only exist on physical spaces, seeded
# separately by create_physical_space. These helpers are the sync mirror of
# core.mutations.array_dataset.create_array_dataset, so a test does not have to go
# through GraphQL to get a well-formed dataset.

import uuid

from asgiref.sync import sync_to_async  # noqa: E402

from core import enums  # noqa: E402
from core.creation import CreationContext  # noqa: E402
from core.logic import coords as coords_logic  # noqa: E402
from core.logic import graph as graph_logic  # noqa: E402
from core.models import ArrayDataset, Axis, CoordinateSystem, DataArray, Lens, Scene  # noqa: E402
from core.inputs.coords import (  # noqa: E402
    AffineTransformInputModel,
    AxisInputModel,
    PhysicalAxisInputModel,
    RegistrationPathInputModel,
    ScaleTransformInputModel,
    TranslationTransformInputModel,
)
from core.logic import coordinate_system as coordinate_system_logic  # noqa: E402


def axis(name: str, type_: enums.AxisType) -> AxisInputModel:
    """One structural axis of a test dataset's pixel grid."""
    return AxisInputModel(name=name, type=type_)


def physical_axis(name: str, type_: enums.AxisType, unit: str) -> PhysicalAxisInputModel:
    """One axis of a unit-carrying (physical) space."""
    return PhysicalAxisInputModel(name=name, type=type_, unit=unit)


#: A 2D multi-channel dataset: the minimum an image layer can render.
SIMPLE_AXES = [
    axis("c", enums.AxisType.CHANNEL),
    axis("y", enums.AxisType.SPACE),
    axis("x", enums.AxisType.SPACE),
]

#: A 3D volume whose axis *names* are the world's, for tests about something other than naming.
#: `SIMPLE_AXES` is (c,y,x) and `create_scene`'s world is (z,y,x), so a per-axis transform between
#: them is refused on its names before anything else about it is looked at -- which is right, and
#: is not what those tests are about.
ZYX_AXES = [
    axis("z", enums.AxisType.SPACE),
    axis("y", enums.AxisType.SPACE),
    axis("x", enums.AxisType.SPACE),
]

#: A bare 2D image, no channel axis.
YX_AXES = [
    axis("y", enums.AxisType.SPACE),
    axis("x", enums.AxisType.SPACE),
]


def _creation(ctx: HttpContext) -> CreationContext:
    return CreationContext(
        user=ctx.request.user,
        organization=ctx.request.organization,
        membership=ctx.request.membership,
        task=None,
    )


def _seed_array_dataset_sync(ctx: HttpContext, name: str, axes: list, shapes: list[list[int]]) -> ArrayDataset:
    """Build a dataset, its coordinate systems, and the edges placing each level in intrinsic pixel space."""
    creation = _creation(ctx)
    axis_specs = [coords_logic.AxisSpec(name=a.name, type=a.type.value) for a in axes]

    # The space, then the data that lives in it.
    intrinsic = CoordinateSystem.objects.create(name=f"{name}/intrinsic", creator=creation.user, organization=creation.organization)
    dataset = ArrayDataset.objects.create(name=name, coordinate_system=intrinsic, creator=creation.user, organization=creation.organization)
    graph_logic.create_pixel_axes(intrinsic, axes)

    for level, shape in enumerate(shapes):
        # Level 0 lives in the dataset's own grid: it IS that grid.
        array_system = intrinsic
        if level:
            array_system = CoordinateSystem.objects.create(name=f"{name}/{level}", creator=creation.user, organization=creation.organization)
        data_array = DataArray.objects.create(level=level, dataset=dataset, coordinate_system=array_system, shape=shape, chunk_shape=shape)
        if level == 0:
            continue
        graph_logic.create_pixel_axes(array_system, axes)
        graph_logic.create_level_edge(
            array_system=array_system,
            intrinsic=intrinsic,
            shape_0=shapes[0],
            shape_level=shape,
            axis_specs=axis_specs,
            ctx=creation,
        )

    return dataset


async def create_array_dataset(ctx: HttpContext, name: str = "ArrayDataset", axes: list | None = None, shapes: list[list[int]] | None = None) -> ArrayDataset:
    """An array dataset with a full coordinate graph. Defaults to a 3x64x64 single-level c/y/x dataset."""
    return await sync_to_async(_seed_array_dataset_sync)(ctx, name, axes or SIMPLE_AXES, shapes or [[3, 64, 64]])


def _seed_physical_space_sync(
    ctx: HttpContext,
    dataset: ArrayDataset,
    axes: list,
    scale: list | None,
    translation: list | None,
    affine: list | None,
    name: str,
) -> CoordinateSystem:
    # The exact path `createCoordinateSystem` runs: a physical space is an ordinary space
    # plus one registration edge, and the transform member IS the kind -- there is no
    # scale+translation sugar (express that as one AFFINE matrix). INFERRED, because a
    # seeded pixel size stands in for numbers read from acquisition metadata.
    if affine is not None:
        transform = AffineTransformInputModel(affine=affine)
    elif scale is not None:
        transform = ScaleTransformInputModel(scale=scale)
    else:
        transform = TranslationTransformInputModel(translation=translation)
    spec = RegistrationPathInputModel(
        transform=transform,
        validity=enums.PlacementValidity.INFERRED,
    )
    return coordinate_system_logic.create_coordinate_system(
        name=f"{dataset.name}/{name}",
        axes=axes,
        registrations=[(dataset.coordinate_system, None, spec)],
        ctx=_creation(ctx),
    )


async def create_physical_space(
    ctx: HttpContext,
    dataset: ArrayDataset,
    axes: list,
    scale: list | None = None,
    translation: list | None = None,
    affine: list | None = None,
    name: str = "physical",
) -> CoordinateSystem:
    """A physical space for a dataset, plus the edge mapping its intrinsic pixels into it."""
    return await sync_to_async(_seed_physical_space_sync)(ctx, dataset, axes, scale, translation, affine, name)


def _seed_lens_sync(ctx: HttpContext, dataset: ArrayDataset, slices: list | None) -> Lens:
    creation = _creation(ctx)
    # An unsliced lens lives in the dataset's own grid: its space IS that space.
    sliced = bool(slices)
    lens_system = dataset.coordinate_system
    if sliced:
        lens_system = CoordinateSystem.objects.create(name=f"{dataset.name}/lens", creator=creation.user, organization=creation.organization)
    lens = Lens.objects.create(dataset=dataset, coordinate_system=lens_system, slices=slices or [])
    if not lens.slices_list:
        return lens
    graph_logic.create_pixel_axes(lens_system, dataset.axes)
    graph_logic.create_lens_edge(
        lens_system=lens_system,
        parent_system=dataset.coordinate_system,
        dataset_axis_names=dataset.axis_names,
        slices=lens.slices_list,
        ctx=creation,
    )
    return lens


async def create_lens(ctx: HttpContext, dataset: ArrayDataset, slices: list | None = None) -> Lens:
    """A lens over an array dataset, with its coordinate system and its edge back to the dataset."""
    return await sync_to_async(_seed_lens_sync)(ctx, dataset, slices)


def _register_into_scene_sync(ctx: HttpContext, scene: Scene, dataset: ArrayDataset | None, system: CoordinateSystem | None):
    """One explicit MANUAL registration: the identity on the axis names shared with the world.

    Layer mutations no longer fabricate placements, so a test that wants a placed layer
    authors the registration first -- exactly the step a real client takes.
    """
    source = system if system is not None else dataset.coordinate_system
    world = scene.world
    world_names = [axis.name for axis in world.axes.all()]
    shared = [axis.name for axis in source.axes.all() if axis.name in world_names]
    return graph_logic.create_identity_registration(
        input_system=source,
        world=world,
        shared=shared,
        name=f"{source.name} -> {scene.name}",
        validity=enums.PlacementValidityChoices.MANUAL.value,
        ctx=_creation(ctx),
    )


async def register_into_scene(ctx: HttpContext, scene: Scene, dataset: ArrayDataset | None = None, *, system: CoordinateSystem | None = None):
    """Register a dataset's intrinsic system (or an explicit system) into a scene's world."""
    return await sync_to_async(_register_into_scene_sync)(ctx, scene, dataset, system)


def _seed_scene_sync(ctx: HttpContext, name: str) -> Scene:
    # An ordinary ownerless SHARED world, adopted by the scene -- the same shape
    # create_scene makes.
    world = CoordinateSystem.objects.create(
        name=f"{name}/world",
        creator=ctx.request.user,
        organization=ctx.request.organization,
    )
    scene = Scene.objects.create(name=name, world=world, organization=ctx.request.organization)
    Axis.objects.bulk_create(
        [
            Axis(coordinate_system=world, order=index, name=n, type=enums.AxisTypeChoices.SPACE.value, unit="micrometer")
            for index, n in enumerate(["z", "y", "x"])
        ]
    )
    return scene


async def create_scene(ctx: HttpContext, name: str = "Scene") -> Scene:
    """A scene with its WORLD coordinate system."""
    return await sync_to_async(_seed_scene_sync)(ctx, name)


#: The grid and encoding a fabriks store's manifest would state. Anisotropic on purpose: a cubic
#: cell size cannot tell a correct component order from a reversed one, so a symmetric fixture
#: passes a transposed implementation.
FABRIKS_GRID = {"cellSize": [128, 128, 64], "levels": 3, "sortKey": "MORTON"}
FABRIKS_ENCODING = {
    "positions": "UINT16_QUANTIZED_PER_CELL",
    "indices": "UINT32",
    "codec": "MESHOPT",
    "compression": "NONE",
    "boundary": "LOCKED",
    "decimation": "QUARTER",
}


def _seed_fabriks_store_sync(ctx: HttpContext, *, axes: list[str] | None, populated: bool):
    """A fabriks store carrying what `fill_info` would have read off its manifest.

    Created directly rather than through the upload path, because no test here has an S3 to
    write a tree to -- and what the tests are about is what the server does with a *finished*
    store, not how the bytes got there.
    """
    from datalayer.models import FabriksStore

    key = f"fabriks-{FabriksStore.objects.count()}"
    return FabriksStore.objects.create(
        path=f"s3://fabriks/{key}",
        bucket="fabriks",
        key=key,
        organization=ctx.request.organization,
        populated=populated,
        spec_version="1" if populated else None,
        grid=FABRIKS_GRID if populated else None,
        encoding=FABRIKS_ENCODING if populated else None,
        axes=axes,
    )


async def create_fabriks_store(ctx: HttpContext, *, axes: list[str] | None = None, populated: bool = True):
    """A finished fabriks store, ready to be registered as a collection."""
    return await sync_to_async(_seed_fabriks_store_sync)(ctx, axes=axes, populated=populated)


KONNEKTION_GRID = {"cellSize": [128, 128, 64], "levels": 1, "sortKey": "MORTON"}
KONNEKTION_ENCODING = {
    "positions": "UINT16_QUANTIZED_PER_CELL",
    "edges": "UINT32_PAIRS",
    "nodeIds": "UINT64",
    "radii": "FLOAT32",
    "ghosts": "TRAILING_PER_OWNER_CELL",
    "codec": "NONE",
    "compression": "NONE",
    "pruning": "NONE",
    "simplification": "NONE",
}

#: What every current konnektion build declares: the four intrinsic metrics, computed on the
#: full level-0 graph. The same shape `fill_info` stores off a real manifest's `attributes`.
KONNEKTION_ATTRIBUTES = [
    {"name": "strahler", "encoding": "FLOAT32", "semantics": "STRAHLER"},
    {"name": "degree", "encoding": "FLOAT32", "semantics": "DEGREE"},
    {"name": "depth", "encoding": "FLOAT32", "semantics": "DEPTH"},
    {"name": "component", "encoding": "FLOAT32", "semantics": "COMPONENT"},
]


def _seed_konnektion_store_sync(ctx: HttpContext, *, axes: list[str] | None, attributes: list[dict] | None, encoding: dict | None, populated: bool):
    """A konnektion store carrying what `fill_info` would have read off its manifest.

    The graph twin of `_seed_fabriks_store_sync`, created directly for the same no-S3 reason.
    ``attributes=None`` is a store filled before attributes existed (or a collection built
    without them), which the picker paths must treat exactly as declaring none.
    """
    from datalayer.models import KonnektionStore

    key = f"konnektion-{KonnektionStore.objects.count()}"
    return KonnektionStore.objects.create(
        path=f"s3://konnektion/{key}",
        bucket="konnektion",
        key=key,
        organization=ctx.request.organization,
        populated=populated,
        spec_version="1" if populated else None,
        grid=KONNEKTION_GRID if populated else None,
        encoding=(encoding if encoding is not None else KONNEKTION_ENCODING) if populated else None,
        attributes=attributes if populated else None,
        axes=axes,
    )


async def create_konnektion_store(
    ctx: HttpContext,
    *,
    axes: list[str] | None = None,
    attributes: "list[dict] | None" = KONNEKTION_ATTRIBUTES,
    encoding: dict | None = None,
    populated: bool = True,
):
    """A finished konnektion store, ready to be registered as a network collection."""
    return await sync_to_async(_seed_konnektion_store_sync)(ctx, axes=axes, attributes=attributes, encoding=encoding, populated=populated)


def _seed_parquet_store_sync(ctx: HttpContext, *, key: str, columns: list[tuple[str, str]] | None = None, populated: bool = True):
    """A parquet store carrying what `fill_info` would have read off the file.

    The same move `_seed_fabriks_store_sync` makes, and now necessary for the same reason: since
    `ParquetStore.fill_info` DESCRIBEs the object, a store left unpopulated makes
    `createTableDataset` reach for an S3 no unit test has. Before that it read nothing, so an
    unfinished store cost nothing and every test here left one behind.

    `columns=None` records a finished store whose schema was not worth stating -- which is most
    tests, because they are about placement and edges rather than about the file. Pass the pairs
    when the test is about the schema itself.
    """
    from datalayer.models import ParquetStore

    return ParquetStore.objects.create(
        path=f"s3://parquet/{key}",
        bucket="parquet",
        key=key,
        organization=ctx.request.organization,
        populated=populated,
        columns=[{"name": name, "type": dtype, "nullable": True} for name, dtype in columns] if columns is not None else None,
    )


async def create_parquet_store(ctx: HttpContext, *, key: str, columns: list[tuple[str, str]] | None = None, populated: bool = True):
    """A finished parquet store, ready to be registered as a table dataset."""
    return await sync_to_async(_seed_parquet_store_sync)(ctx, key=key, columns=columns, populated=populated)


def index_axis(columns: list[dict]) -> str:
    """The single INDEX coordinate column -- the one a keying source lands on.

    A source keys by supplying ids, and an id is looked up in an enumeration, so the axis it
    produces is the INDEX one. Derived rather than named at each call site: it is a fact about
    the column declaration, and the tests that migrated off `keyedBy` were all naming it by
    hand from the same three fixtures.
    """
    index = [c["name"] for c in columns if c.get("role") == "COORDINATE" and c.get("axisType") == "INDEX"]
    if len(index) != 1:
        raise AssertionError(f"expected exactly one INDEX coordinate column, got {index}")
    return index[0]


def flat_columns(
    columns: list[dict],
    *,
    identified_by: dict[str, list] | None = None,
    keyed_by: list | None = None,
) -> list[dict]:
    """The one `columns` list `createTableDataset` takes, from a fixture's column dicts.

    The fixtures were already flat -- one dict per column with a per-column ``axisType`` --
    and the old helpers existed only to SPLIT them into the two wire lists the API used to
    want. The wire is flat now too (axis-ness is `axisType` on the column, identification is
    `identifiedBy`, the old `references` field is a TABLE identification), so this is nearly
    the identity: the legacy ``role: COORDINATE`` marker becomes the bare ``axisType``, and a
    legacy ``references`` becomes ``identifiedBy: [{kind: TABLE}]``.

    ``identified_by`` names sources per column; ``keyed_by`` is the shorthand for the common
    case, putting them on the single INDEX axis. Columns named in neither get no
    ``identifiedBy``, which is legal and ordinary (a localization table's `x` axis is
    identified by nothing).
    """
    sources = dict(identified_by or {})
    if keyed_by:
        # The convenience the migration off `keyedBy` needed: put these sources on the axis a
        # keying source produces, which is the INDEX one.
        sources.setdefault(index_axis(columns), keyed_by)

    declared = []
    for column in columns:
        entry: dict = {"name": column["name"], "dtype": column.get("dtype", "DOUBLE")}
        if column.get("role") == "COORDINATE":
            # COORDINATE is not a role the wire may claim: it follows from `axisType`.
            entry["axisType"] = column["axisType"]
        elif column.get("role") is not None:
            entry["role"] = column["role"]
        for key in ("unit", "longName", "description"):
            if column.get(key) is not None:
                entry[key] = column[key]
        identifications = list(sources.get(column["name"], []))
        if column.get("references") is not None:
            # The retired field, spelled the one remaining way.
            identifications.append({"kind": "TABLE", "table": column["references"]})
        if identifications:
            entry["identifiedBy"] = identifications
        declared.append(entry)
    return declared


def split_declaration(columns: list[dict]) -> tuple[list[tuple[str, str]], list[dict]]:
    """The store's schema and the wire's column list, from one fixture constant.

    Named for the split it used to perform into `axes` + `columns`; what survives of the
    split is the one real division left -- the file's own account (``store_columns``, what
    ``fill_info`` records) versus the declaration checked against it.

    Returns:
        ``(store_columns, columns)`` -- the store's `[(name, duckdb type)]` in file order,
        and the flat `ColumnInput` dicts in the same order.
    """
    store_columns = [(column["name"], column.get("dtype", "DOUBLE")) for column in columns]
    return store_columns, flat_columns(columns)


async def table_input(
    ctx: HttpContext,
    name: str,
    columns: list[dict],
    *,
    identified_by: dict[str, list] | None = None,
    keyed_by: list | None = None,
    **extra: object,
) -> dict:
    """Everything `createTableDataset` needs, from one fixture-shaped column list.

    The mutation reads a column's name and type off the **file** and takes only what the file
    cannot say, so a test's one constant becomes two things: the store's own schema and the
    flat declaration. This also creates the store carrying that schema, which is load-bearing
    -- `columns_for_store` reads it on every create, and a store without it makes the create
    reach for an S3 no unit test has.
    """
    store_columns = [(column["name"], column.get("dtype", "DOUBLE")) for column in columns]
    # Unique, because two tables of the same name in one test are ordinary and the store path
    # is unique-constrained.
    store = await create_parquet_store(ctx, key=f"{name.replace(' ', '-')}-{uuid.uuid4().hex[:8]}", columns=store_columns)
    return {
        "name": name,
        "data": str(store.pk),
        "columns": flat_columns(columns, identified_by=identified_by, keyed_by=keyed_by),
        **extra,
    }


def split_payload(columns: list[dict], *, identified_by: dict[str, list] | None = None, keyed_by: list | None = None) -> dict:
    """The `columns` half of a create payload, for a test that builds its store itself.

    :func:`table_input` is the whole payload and creates the store; this is the same shape for
    the call sites that already have a store in hand. The store still has to carry the file's
    schema -- see :func:`create_parquet_store` -- or the create has nothing to infer from.
    """
    return {"columns": flat_columns(columns, identified_by=identified_by, keyed_by=keyed_by)}


def sparse_layout(axis: int, rank: int = 2, nnz: int = 96) -> dict:
    """One entry of a sparse store's `layouts`, as `finishSparseUpload` would have recorded it."""
    return {
        "path": sparse_layout_path(axis),
        "encoding": ("csr_matrix" if axis == 0 else "csc_matrix") if rank == 2 else "csr_matrix",
        "encoding_version": "0.1.0",
        "indexed_axis": axis,
        "index_order": [other for other in range(rank) if other != axis],
        "nnz": nnz,
        "dtype": "float32",
        "chunks": {"data": 32768, "indices": 32768, "indptr": 32768},
        "range_readable": False,
    }


async def create_sparse_store(ctx: HttpContext, key: str, *, axes: tuple[int, ...] = (0,), shape: list[int]) -> SparseStore:
    """A finished sparse store holding a layout per axis in ``axes``, built directly.

    **One matrix is one upload**, so a store is a whole matrix in one or more layouts rather than
    one layout apiece. `fill_info` reads the prefix off S3; setting the fields here says the same
    thing more plainly, and what is on trial is what a mutation does with a store's declared facts.
    """
    extents = list(shape)
    return await sync_to_async(SparseStore.objects.create)(
        path=f"s3://zarr/{key}",
        bucket="zarr",
        key=key,
        organization=ctx.request.organization,
        populated=True,
        spec="1",
        shape=extents,
        layouts=[sparse_layout(axis, rank=len(extents)) for axis in axes],
    )
