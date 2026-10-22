import logging
from collections.abc import Callable
from weakref import WeakSet

from django.apps import apps
from django.db import transaction
from django.db.models import Model, QuerySet
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from kante.channel import Channel

from core import channels, models
from core import derived
from core import descriptors as descriptors_logic
from core.channel_signals import RowSignal

logger = logging.getLogger(__name__)


def _file_rooms(instance: models.File) -> list[str]:
    return [
        channels.org_files_room(instance.organization_id),
        channels.folder_files_room(instance.folder_id),
    ]


@receiver(post_save, sender=models.File)
def my_file_handler(sender, instance=None, created=None, **kwargs):
    if created:
        channels.file_channel.broadcast(channels.FileSignal(create=instance.id), _file_rooms(instance))
    else:
        channels.file_channel.broadcast(channels.FileSignal(update=instance.id), _file_rooms(instance))


@receiver(pre_delete, sender=models.File)
def my_file_delete_handler(sender, instance=None, **kwargs):
    channels.file_channel.broadcast(channels.FileSignal(delete=instance.id), _file_rooms(instance))


@receiver(post_save, sender=models.Annotation)
def annotation_saved_handler(sender, instance=None, created=None, **kwargs):
    signal = channels.AnnotationSignal(create=str(instance.id)) if created else channels.AnnotationSignal(update=str(instance.id))
    channels.announce_annotations(signal, instance.collection_id)


# pre_delete, because the id is gone after it -- and it fires per row for a cascade too,
# so deleting a collection tells its subscribers about every shape that went with it.
@receiver(pre_delete, sender=models.Annotation)
def annotation_deleted_handler(sender, instance=None, **kwargs):
    channels.announce_annotations(channels.AnnotationSignal(delete=str(instance.id)), instance.collection_id)


# --- descriptors that follow other rows ----------------------------------------------
# What a dataset's values mean follows the derivation edges leaving its grid, and how many shapes
# a collection holds follows the shapes drawn into it: neither is a save of the row described, so
# the row is restored when they change. A bulk write of shapes restores by hand (`create_annotations`).


@receiver(post_save, sender=models.Transformation)
@receiver(post_delete, sender=models.Transformation)
def edge_written_handler(sender: type[models.Transformation], instance: models.Transformation, **kwargs: object) -> None:
    descriptors_logic.restore_value_kind([instance.input_id])


@receiver(post_save, sender=models.Annotation)
@receiver(post_delete, sender=models.Annotation)
def annotation_written_handler(sender: type[models.Annotation], instance: models.Annotation, **kwargs: object) -> None:
    descriptors_logic.restore_annotation_counts([instance.collection_id])


# --- columns derived from the anchors --------------------------------------------------
# A container's derived columns (core.derived) follow the spokes they are read from: a spoke a
# deriver names, saved or deleted, or an anchor deleted, derives its container again. In the
# same transaction, so the columns commit with the anchors they summarise.


def _deriving_write(origin: object) -> bool:
    """Whether a delete started at an anchor or a spoke, rather than cascading from the container -- which is going away, and must not gain conflict rows on its way."""
    if origin is None:
        return True
    model = origin.model if isinstance(origin, QuerySet) else type(origin)
    return issubclass(model, models.CoordinateAnchor) or model._meta.label in derived.SOURCES


def _rederive_anchor_container(anchor: models.CoordinateAnchor | None) -> None:
    container = anchor.container if anchor is not None else None
    if container is not None and derived.derived_fields(type(container)):
        derived.request(container)


def spoke_written_handler(sender: type[Model], instance: Model, raw: bool = False, origin: object = None, **kwargs: object) -> None:
    if raw or not _deriving_write(origin):
        return
    # The spoke's own anchor, and through it the container instance the writer holds: a
    # mutation returning that container answers with the columns just derived.
    try:
        anchor = instance.anchor
    except models.CoordinateAnchor.DoesNotExist:
        return
    _rederive_anchor_container(anchor)


@receiver(post_delete, sender=models.CoordinateAnchor)
def anchor_deleted_handler(sender: type[models.CoordinateAnchor], instance: models.CoordinateAnchor, origin: object = None, **kwargs: object) -> None:
    if _deriving_write(origin):
        _rederive_anchor_container(instance)


for _label in sorted(derived.SOURCES):
    post_save.connect(spoke_written_handler, sender=apps.get_model(_label), dispatch_uid=f"rederive-saved-{_label}")
    post_delete.connect(spoke_written_handler, sender=apps.get_model(_label), dispatch_uid=f"rederive-deleted-{_label}")


def _relay_rows[M: Model](model: type[M], channel: Channel[RowSignal], rooms: Callable[[M], list[str]]) -> None:
    """Announce every save and delete of ``model`` to the rooms its row belongs in.

    ``post_save`` and ``pre_delete`` only: a ``bulk_create`` or a queryset ``update()`` of
    one of these models passes unannounced. No mutation writes them that way, but the
    ``SET_NULL`` of a deleted folder or default scene does.
    ``pre_delete`` because the id is gone after it, and it fires per row for a cascade too.
    """
    # Rows created in a transaction that is still open. A create mutation often saves its
    # row again before it commits (a table adopting its coordinate system); the subscriber
    # re-fetches on the create and sees the final state, so that update says nothing new.
    # Weak, so a rolled-back create is forgotten with its instance.
    uncommitted: WeakSet[M] = WeakSet()

    def saved(sender: type[M], instance: M, created: bool, **kwargs: object) -> None:
        if created:
            uncommitted.add(instance)
            transaction.on_commit(lambda: uncommitted.discard(instance))
            channels.announce(channel, RowSignal(create=instance.pk), rooms(instance))
        elif instance not in uncommitted:
            channels.announce(channel, RowSignal(update=instance.pk), rooms(instance))

    def deleted(sender: type[M], instance: M, **kwargs: object) -> None:
        channels.announce(channel, RowSignal(delete=instance.pk), rooms(instance))

    # weak=False: the receivers are closures, and nothing else holds on to them.
    post_save.connect(saved, sender=model, weak=False)
    pre_delete.connect(deleted, sender=model, weak=False)


def _array_dataset_rooms(dataset: models.ArrayDataset) -> list[str]:
    rooms = [channels.org_array_datasets_room(dataset.organization_id)]
    if dataset.folder_id is not None:
        rooms.append(channels.folder_array_datasets_room(dataset.folder_id))
    return rooms


def _table_dataset_rooms(dataset: models.TableDataset) -> list[str]:
    rooms = [channels.org_table_datasets_room(dataset.organization_id)]
    if dataset.folder_id is not None:
        rooms.append(channels.folder_table_datasets_room(dataset.folder_id))
    return rooms


_relay_rows(models.Layer, channels.layer_channel, lambda layer: [channels.scene_layers_room(layer.scene_id)])
_relay_rows(models.ArrayDataset, channels.array_dataset_channel, _array_dataset_rooms)
_relay_rows(models.TableDataset, channels.table_dataset_channel, _table_dataset_rooms)
_relay_rows(models.Scene, channels.scene_channel, lambda scene: [channels.org_scenes_room(scene.organization_id)])


def _lens_rooms(lens: models.Lens) -> list[str]:
    return [channels.org_lenses_room(lens.organization_id), channels.container_lenses_room(type(lens).KIND, lens.container_id)]


# Per concrete kind: `post_save` fires with the concrete class as sender and never for the
# polymorphic base, so a relay connected on `Lens` would hear nothing.
for _kind in models.Lens.KIND_BY_CONTAINER.values():
    _relay_rows(_kind, channels.lens_channel, _lens_rooms)
