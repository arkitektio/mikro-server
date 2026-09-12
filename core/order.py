import strawberry_django
from core import models
from koherent.models import Task as KoherentTask
from strawberry import auto


@strawberry_django.order_type(models.Folder)
class FolderOrder:
    created_at: auto
    name: auto
    id: auto


@strawberry_django.order_type(models.File)
class FileOrder:
    created_at: auto
    name: auto
    size: auto
    content_type: auto
    id: auto


@strawberry_django.order_type(models.FileLink)
class FileLinkOrder:
    """Ordering for file links."""

    created_at: auto
    direction: auto
    id: auto


@strawberry_django.order_type(models.ArrayDataset)
class ArrayDatasetOrder:
    created_at: auto
    name: auto
    id: auto


@strawberry_django.order_type(models.Animation)
class AnimationOrder:
    created_at: auto
    name: auto
    id: auto


@strawberry_django.order_type(models.SceneSnapshot)
class SceneSnapshotOrder:
    created_at: auto
    name: auto
    id: auto


@strawberry_django.order_type(models.DataArray)
class DataArrayOrder:
    level: auto
    id: auto


@strawberry_django.order_type(models.Annotation)
class AnnotationOrder:
    name: auto
    id: auto


@strawberry_django.order_type(models.AnnotationCollection)
class AnnotationCollectionOrder:
    name: auto
    created_at: auto
    id: auto


@strawberry_django.order_type(models.Lens)
class LensOrder:
    id: auto


@strawberry_django.order_type(models.Layer)
class LayerOrder:
    id: auto


@strawberry_django.order_type(models.Scene)
class SceneOrder:
    name: auto
    id: auto


@strawberry_django.order_type(KoherentTask)
class TaskOrder:
    created_at: auto
    id: auto


@strawberry_django.order_type(models.CoordinateSystem)
class CoordinateSystemOrder:
    name: auto
    created_at: auto
    id: auto


@strawberry_django.order_type(models.Transformation)
class TransformationOrder:
    order: auto
    created_at: auto
    id: auto


@strawberry_django.order_type(models.MeshCollection)
class MeshCollectionOrder:
    version: auto
    created_at: auto
    id: auto


@strawberry_django.order_type(models.SparseDataset)
class SparseDatasetOrder:
    name: auto
    created_at: auto
    id: auto


@strawberry_django.order_type(models.TableDataset)
class TableDatasetOrder:
    name: auto
    created_at: auto
    id: auto
