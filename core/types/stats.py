"""The aggregates behind the home page's page list: how many of each kind there are.

One per root list the UI has a page for, built like ``ArrayDatasetStats`` (see
``create_stats_type`` for the memoized aggregates and the org scoping, which is the same
``scope_queryset`` the lists go through, so a count is the length of its page). Each
takes its list's filter, so ``filters: {createdAfter: ...}`` is "new this week".

Annotations and scenes carry no timestamp: they count, but have no series and nothing to
narrow "new" by.
"""

from core import filters, models
from core.type_gen import create_stats_type

CREATED_AT = {"created_at": "created_at"}

LensStats, LensStatsResolver = create_stats_type(
    models.Lens,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields=CREATED_AT,
    filters=filters.LensFilter,
)

CoordinateSystemStats, CoordinateSystemStatsResolver = create_stats_type(
    models.CoordinateSystem,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields=CREATED_AT,
    filters=filters.CoordinateSystemFilter,
)

TableDatasetStats, TableDatasetStatsResolver = create_stats_type(
    models.TableDataset,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields=CREATED_AT,
    filters=filters.TableDatasetFilter,
)

SparseDatasetStats, SparseDatasetStatsResolver = create_stats_type(
    models.SparseDataset,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields=CREATED_AT,
    filters=filters.SparseDatasetFilter,
)

FolderStats, FolderStatsResolver = create_stats_type(
    models.Folder,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields=CREATED_AT,
    filters=filters.FolderFilter,
)

FileStats, FileStatsResolver = create_stats_type(
    models.File,
    allowed_fields={"pk": "id"},
    allowed_datetime_fields=CREATED_AT,
    filters=filters.FileFilter,
)

AnnotationStats, AnnotationStatsResolver = create_stats_type(
    models.Annotation,
    allowed_fields={"pk": "id"},
    filters=filters.AnnotationFilter,
)

SceneStats, SceneStatsResolver = create_stats_type(
    models.Scene,
    allowed_fields={"pk": "id"},
    filters=filters.SceneFilter,
)
