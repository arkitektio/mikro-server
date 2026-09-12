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
# of the domain on top of it (guarded by tests/test_architecture.py). All
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
    MeshCollection,
    NetworkCollection,
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
    LightPath,
    PhasorHistogram,
    PhasorCalibration,
    Lens,
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
    "MeshCollection",
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
    "LightPath",
    "PhasorHistogram",
    "PhasorCalibration",
    "Lens",
    "Scene",
    "SceneSnapshot",
    "Animation",
    "AnimationWaypoint",
    "Layer",
]
