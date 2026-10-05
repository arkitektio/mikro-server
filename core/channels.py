from django.db import transaction
from kante.channel import Channel, build_channel
from pydantic import BaseModel

from .channel_signals import AnnotationSignal, FileSignal, RowSignal

file_channel = build_channel(FileSignal)
annotation_channel = build_channel(AnnotationSignal)
# One payload shape, four channels: the explicit name is what keeps them apart, since
# channels built from the same model share a message type otherwise.
layer_channel = build_channel(RowSignal, name="LayerSignal")
array_dataset_channel = build_channel(RowSignal, name="ArrayDatasetSignal")
table_dataset_channel = build_channel(RowSignal, name="TableDatasetSignal")
scene_channel = build_channel(RowSignal, name="SceneSignal")


# Room names are built in one place so the broadcasting signals and the
# subscribing resolvers cannot drift apart. The org-wide rooms carry the
# organization id so cross-tenant events never share a room.


def org_files_room(org_id: int) -> str:
    return f"org_{org_id}_files"


def folder_files_room(folder_id: int) -> str:
    return f"folder_files_{folder_id}"


def collection_annotations_room(collection_id: int) -> str:
    return f"collection_annotations_{collection_id}"


def scene_layers_room(scene_id: int | str) -> str:
    return f"scene_layers_{scene_id}"


def org_array_datasets_room(org_id: int) -> str:
    return f"org_{org_id}_array_datasets"


def folder_array_datasets_room(folder_id: int | str) -> str:
    return f"folder_array_datasets_{folder_id}"


def org_table_datasets_room(org_id: int) -> str:
    return f"org_{org_id}_table_datasets"


def folder_table_datasets_room(folder_id: int | str) -> str:
    return f"folder_table_datasets_{folder_id}"


def org_scenes_room(org_id: int) -> str:
    return f"org_{org_id}_scenes"


def announce[S: BaseModel](channel: Channel[S], signal: S, rooms: list[str]) -> None:
    """Tell the subscribers of ``rooms`` about a change, once it is committed.

    On commit, not at once: the subscriber re-fetches the row by id, and a write inside a
    still-open transaction would hand it an id it cannot read yet (or one that rolls back).
    Robust, because the write has already happened by then: a channel layer that is down
    costs the live viewers an event, it must not fail the mutation that made the change.
    """
    transaction.on_commit(lambda: channel.broadcast(signal, rooms), robust=True)


def announce_annotations(signal: AnnotationSignal, collection_id: int) -> None:
    """Tell a collection's subscribers about a change to its annotations, once it is committed."""
    announce(annotation_channel, signal, [collection_annotations_room(collection_id)])
