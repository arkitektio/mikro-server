from kante.types import Info
from core import scalars
import strawberry
import kante
from pydantic import BaseModel, Field
from core import types, models
from core.creation import CreationContext
from core.scoping import get_for_org
from core.mutations._generic import make_delete, make_pin, self_owner


class SceneSnapshotInputModel(BaseModel):
    file: str = Field(description="The uploaded media file store containing the rendered image")
    scene: str = Field(description="The ID of the scene this is a picture of")
    name: str | None = Field(default=None, description="The name of the snapshot")


@kante.pydantic_input(SceneSnapshotInputModel, description="Input for creating a snapshot (a pre-rendered picture) of a scene from an already-uploaded media file")
class SceneSnapshotInput:
    """Input for creating a snapshot (a pre-rendered picture) of a scene from an already-uploaded media file"""

    file: scalars.ImageFileLike = strawberry.field(description="The uploaded media file store containing the rendered image. Upload it first with requestMediaUpload / finishMediaUpload; this only adopts it")
    scene: strawberry.ID = strawberry.field(description="The ID of the scene this is a picture of")
    name: str | None = strawberry.field(default=None, description="The name of the snapshot")


class DeleteSceneSnapshotInputModel(BaseModel):
    id: str = Field(description="The ID of the snapshot to delete")


@kante.pydantic_input(DeleteSceneSnapshotInputModel, description="Input for deleting a lens snapshot by ID")
class DeleteSceneSnapshotInput:
    """Input for deleting a lens snapshot by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the snapshot to delete")


class PinSceneSnapshotInputModel(BaseModel):
    id: str = Field(description="The ID of the snapshot to pin or unpin")
    pin: bool = Field(description="True to pin, false to unpin")


@kante.pydantic_input(PinSceneSnapshotInputModel, description="Input for pinning or unpinning a lens snapshot for quick access")
class PinSceneSnapshotInput:
    """Input for pinning or unpinning a lens snapshot for quick access"""

    id: strawberry.ID = strawberry.field(description="The ID of the snapshot to pin or unpin")
    pin: bool = strawberry.field(description="True to pin, false to unpin")


pin_scene_snapshot = make_pin(models.SceneSnapshot, PinSceneSnapshotInput, types.SceneSnapshot)


# self_owner, not creator_owner: this model declares created_through_by. The test is
# whether the column is there, not whether the model is provenance-tracked -- a
# ProvenanceField does not add it.
delete_scene_snapshot = make_delete(models.SceneSnapshot, DeleteSceneSnapshotInput, owner=self_owner)


def create_scene_snapshot(
    info: Info,
    input: SceneSnapshotInput,
) -> types.SceneSnapshot:
    """Adopt an uploaded media file as the picture of a scene.

    The bytes are already in S3 by the time this runs: the client uploads through
    requestMediaUpload / finishMediaUpload, and this only claims the resulting store.

    Both the store and the scene are fetched org-scoped rather than assigned by raw id,
    so a caller cannot hang a snapshot off another organization's scene.

    What the picture actually shows is not checked against the scene's contents. A
    snapshot records a render that already happened, and the composition may have gained
    or lost layers since -- which is exactly why the picture is worth keeping.
    """
    parsed = input.to_pydantic()
    media_store = get_for_org(models.MediaStore, info, id=parsed.file)
    scene = get_for_org(models.Scene, info, id=parsed.scene)

    ctx = CreationContext.from_info(info)
    item = models.SceneSnapshot.objects.create(
        name=parsed.name or "Snapshot",
        store=media_store,
        scene=scene,
        creator=ctx.user,
        organization=ctx.organization,
        **ctx.provenance_kwargs(),
    )
    return item
