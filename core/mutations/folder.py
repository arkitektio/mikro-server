from kante.types import Info
import kante
import strawberry
from pydantic import BaseModel, Field
from core import types, models, inputs
from typing import Callable, cast
from core.creation import CreationContext
from core.scoping import get_for_org
from core.logic import folder as folder_logic
from core.mutations._generic import make_delete, make_pin, self_owner
from django.db.models import Model


class CreateFolderInputModel(BaseModel):
    name: str = Field(description="The name of the folder")
    parent: str | None = Field(default=None, description="The ID of the parent folder to nest this folder under")


@kante.pydantic_input(CreateFolderInputModel, description="Input for creating a new folder to organize images and files")
class CreateFolderInput:
    """Input for creating a new folder to organize images and files"""

    name: str = strawberry.field(description="The name of the folder")
    parent: strawberry.ID | None = strawberry.field(default=None, description="The ID of the parent folder to nest this folder under")


class DeleteFolderInputModel(BaseModel):
    id: str = Field(description="The ID of the folder to delete")


@kante.pydantic_input(DeleteFolderInputModel, description="Input for deleting a folder by ID")
class DeleteFolderInput:
    """Input for deleting a folder by ID"""

    id: strawberry.ID = strawberry.field(description="The ID of the folder to delete")


class PinFolderInputModel(BaseModel):
    id: str = Field(description="The ID of the folder to pin or unpin")
    pin: bool = Field(description="True to pin, false to unpin")


@kante.pydantic_input(PinFolderInputModel, description="Input for pinning or unpinning a folder for quick access")
class PinFolderInput:
    """Input for pinning or unpinning a folder for quick access"""

    id: strawberry.ID = strawberry.field(description="The ID of the folder to pin or unpin")
    pin: bool = strawberry.field(description="True to pin, false to unpin")


pin_folder = make_pin(models.Folder, PinFolderInput, types.Folder)


class ChangeFolderInputModel(CreateFolderInputModel):
    id: str = Field(description="The ID of the folder to change")


@kante.pydantic_input(ChangeFolderInputModel, description="Input for changing an existing folder's name or parent")
class ChangeFolderInput(CreateFolderInput):
    """Input for changing an existing folder's name or parent"""

    id: strawberry.ID = strawberry.field(description="The ID of the folder to change")


class RevertInputModel(BaseModel):
    id: str = Field(description="The ID of the folder to revert")
    history_id: str = Field(description="The ID of the provenance history entry to revert the folder to")


@kante.pydantic_input(RevertInputModel, description="Input for reverting a folder to a previous history revision")
class RevertInput:
    """Input for reverting a folder to a previous history revision"""

    id: strawberry.ID = strawberry.field(description="The ID of the folder to revert")
    history_id: strawberry.ID = strawberry.field(description="The ID of the provenance history entry to revert the folder to")


def create_folder(
    info: Info,
    input: CreateFolderInput,
) -> types.Folder:
    parsed = input.to_pydantic()
    assert info.context.request.user, "User not authenticated"
    ctx = CreationContext.from_info(info)
    view = models.Folder.objects.create(
        name=parsed.name,
        parent_id=parsed.parent if parsed.parent else None,
        creator=ctx.user,
        organization=ctx.organization,
        membership=ctx.membership,
        **ctx.provenance_kwargs(),
    )
    return cast(types.Folder, view)


def ensure_folder(
    info: Info,
    input: CreateFolderInput,
) -> types.Folder:
    parsed = input.to_pydantic()
    ctx = CreationContext.from_info(info)
    view, _ = models.Folder.objects.get_or_create(
        name=parsed.name,
        parent_id=parsed.parent if parsed.parent else None,
        creator=ctx.user,
        organization=ctx.organization,
        membership=ctx.membership,
        defaults=ctx.provenance_kwargs(),
    )
    return cast(types.Folder, view)


delete_folder = make_delete(models.Folder, DeleteFolderInput, owner=self_owner)


def update_folder(
    info: Info,
    input: ChangeFolderInput,
) -> types.Folder:
    parsed = input.to_pydantic()
    view = get_for_org(models.Folder, info,
        id=parsed.id,
    )
    view.name = parsed.name
    view.save()
    return view


def revert_folder(
    info: Info,
    input: RevertInput,
) -> types.Folder:
    parsed = input.to_pydantic()
    folder = get_for_org(models.Folder, info,
        id=parsed.id,
    )
    historic = folder.history.get(history_id=parsed.history_id)
    historic.instance.save()
    return historic.instance


def put_folders_in_folder(
    info: Info,
    input: inputs.AssociateInput,
) -> types.Folder:
    parsed = input.to_pydantic()
    parent = get_for_org(models.Folder, info,
        id=parsed.other,
    )

    for i in parsed.selfs:
        folder = get_for_org(models.Folder, info,
            id=i,
        )
        folder.parent = parent
        folder.save()

    return parent


def release_folders_from_folder(
    info: Info,
    input: inputs.DesociateInput,
) -> types.Folder:
    parsed = input.to_pydantic()
    for i in parsed.selfs:
        folder = get_for_org(models.Folder, info,
            id=i,
        )
        folder.parent = None
        folder.save()
    return folder


def _make_put_in_folder(model: type[Model]) -> Callable:
    """Build the `put<Things>InFolder` resolver for one fileable model.

    Eight near-identical resolvers otherwise, in four pairs that differ only in the model
    they scope. The house already builds resolvers this way -- `make_delete` and `make_pin`
    in `_generic` -- and the alternative here was eight copies of five lines.
    """

    def put_in_folder(info: Info, input: inputs.AssociateInput) -> types.Folder:
        parsed = input.to_pydantic()
        folder = get_for_org(models.Folder, info, id=parsed.other)
        items = [get_for_org(model, info, id=identifier) for identifier in parsed.selfs]
        # Refused before anything moves: a request naming one derived item must not leave
        # the items ahead of it already re-filed.
        for item in items:
            folder_logic.assert_explicitly_fileable(item)
        for item in items:
            folder_logic.refile(item, folder)
        return folder

    return put_in_folder


def _make_release_from_folder(model: type[Model]) -> Callable:
    """Build the `release<Things>FromFolder` resolver for one fileable model.

    Unfiling, not deleting -- the same statement `on_delete=SET_NULL` makes for a folder
    that goes away. Every `folder` column is nullable, which is what lets this be a
    statement about filing rather than about existence.
    """

    def release_from_folder(info: Info, input: inputs.DesociateInput) -> types.Folder:
        parsed = input.to_pydantic()
        folder = get_for_org(models.Folder, info, id=parsed.other)
        items = [get_for_org(model, info, id=identifier) for identifier in parsed.selfs]
        for item in items:
            folder_logic.assert_explicitly_fileable(item)
        for item in items:
            folder_logic.refile(item, None)
        return folder

    return release_from_folder


put_files_in_folder = _make_put_in_folder(models.File)
release_files_from_folder = _make_release_from_folder(models.File)

put_array_datasets_in_folder = _make_put_in_folder(models.ArrayDataset)
release_array_datasets_from_folder = _make_release_from_folder(models.ArrayDataset)

put_table_datasets_in_folder = _make_put_in_folder(models.TableDataset)
release_table_datasets_from_folder = _make_release_from_folder(models.TableDataset)

put_mesh_collections_in_folder = _make_put_in_folder(models.MeshCollection)
release_mesh_collections_from_folder = _make_release_from_folder(models.MeshCollection)

put_annotation_collections_in_folder = _make_put_in_folder(models.AnnotationCollection)
release_annotation_collections_from_folder = _make_release_from_folder(models.AnnotationCollection)
