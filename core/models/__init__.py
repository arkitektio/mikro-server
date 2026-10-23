"""Django models for the core app, split by domain.

Every public name is re-exported here so that ``from core import models``
and ``from core.models import X`` keep working everywhere (including
migrations, which reference e.g. ``core.models.create_default_color``).
"""

# Re-exported store models: the old monolithic ``core/models.py`` imported
# these at module level, and code such as ``core/filters.py`` and the
# mutations access them as ``models.ZarrStore`` etc.
#
# Dependency rule between the apps: core may depend on datalayer models (they
# are FK targets of Image/File/Table and re-exported here for convenience),
# but datalayer must never import core — it is the storage backend, agnostic
# of the domain on top of it. All
# runtime storage I/O goes through ``datalayer.datalayer.get_current_datalayer()``,
# never through boto3/zarr directly.
from datalayer.models import (
    SparseStore,
    ZarrStore,
    BigFileStore,
    ParquetStore,
    MediaStore,
    FabriksStore,
    KonnektionStore,
)

from .folder import (
    FolderManager,
    Folder,
    File,
    FileLink,
)
from .meta import (
    MetaSchema,
    UnstructuredMeta,
)
from .coords import (
    CoordinateSystem,
    Axis,
    Transformation,
    TransformationField,
    MeshCollection,
    NetworkCollection,
    Fixture,
)
from .table_dataset import (
    TableDataset,
    Column,
)
from .sparse_dataset import (
    SparseDataset,
    SparseArray,
    SparseAxisReference,
)
from .annotation import (
    AnnotationCollection,
    Annotation,
)
from .array_dataset import (
    ArrayDataset,
    DataArray,
    CoordinateAnchor,
    OptikitState,
    OmeMetadata,
    ValueHistogram,
    ChannelLabel,
    AcquisitionWindow,
    DerivationConflict,
    LightPath,
    PhasorHistogram,
    PhasorCalibration,
    Lens,
    ArrayLens,
    LensCut,
    TableLens,
    SparseLens,
    MeshLens,
    NetworkLens,
    AnnotationLens,
    FixtureLens,
    Scene,
    SceneSnapshot,
    Animation,
    AnimationWaypoint,
    Layer,
)

__all__ = [
    # datalayer stores (re-exported for backwards compatibility)
    "ZarrStore",
    "BigFileStore",
    "ParquetStore",
    "FabriksStore",
    "KonnektionStore",
    "MediaStore",
    # folder
    "FolderManager",
    "Folder",
    "File",
    "FileLink",
    # meta
    "MetaSchema",
    "UnstructuredMeta",
    # coords (the RFC-5 coordinate system graph)
    "CoordinateSystem",
    "Axis",
    "Transformation",
    "TransformationField",
    "MeshCollection",
    "Fixture",
    "NetworkCollection",
    # table dataset
    "TableDataset",
    "Column",
    # sparse dataset
    "SparseDataset",
    "SparseArray",
    "SparseAxisReference",
    "SparseStore",
    # annotations
    "AnnotationCollection",
    "Annotation",
    # array_dataset
    "ArrayDataset",
    "DataArray",
    "CoordinateAnchor",
    "OptikitState",
    "OmeMetadata",
    "ValueHistogram",
    "ChannelLabel",
    "AcquisitionWindow",
    "DerivationConflict",
    "LightPath",
    "PhasorHistogram",
    "PhasorCalibration",
    "Lens",
    "ArrayLens",
    "LensCut",
    "TableLens",
    "SparseLens",
    "MeshLens",
    "FixtureLens",
    "NetworkLens",
    "AnnotationLens",
    "Scene",
    "SceneSnapshot",
    "Animation",
    "AnimationWaypoint",
    "Layer",
]

# The one registry of lens kinds: which lens model selects over which container, written here
# because this is the first place every container model exists. Everything that enumerates the
# kinds derives from it -- the coordinate graph's lens containers, the merge job's rounds, the
# subscriptions' and filters' kind lookups -- so a seventh kind is one line here, plus its model.
Lens.KIND_BY_CONTAINER.update(
    {
        ArrayDataset: ArrayLens,
        TableDataset: TableLens,
        SparseDataset: SparseLens,
        MeshCollection: MeshLens,
        Fixture: FixtureLens,
        NetworkCollection: NetworkLens,
        AnnotationCollection: AnnotationLens,
    }
)
