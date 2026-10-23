"""Mutations for fixtures.

A model is one uploaded binary glTF in a media store, living in a unit-carrying space of its
own. Registering it is naming that file and the axes its vertices are in; the box it occupies
is read from the file, never declared. Like a collection it is immutable: a revised drawing is
a new model over a new file.
"""

from django.db import transaction
from kante.types import Info
import strawberry
from pydantic import BaseModel, Field
import kante

from core import models, scalars, types
from core.creation import CreationContext
from core.input_unions import prose_errors
from core.inputs.coords import DerivedFromInput, DerivedFromSpec, PhysicalAxisInput, PhysicalAxisInputModel
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import folder as folder_logic
from core.logic import gltf as gltf_logic
from core.logic import graph as graph_logic
from core.mutations._generic import make_delete, self_owner
from core.scoping import get_for_org
from datalayer.datalayer import get_current_datalayer


class CreateFixtureInputModel(BaseModel):
    name: str | None = None
    store: str
    axes: list[PhysicalAxisInputModel]
    folder: str | None = None
    derived_from: list[DerivedFromSpec] | None = None
    provenance_metadata: dict | None = None


@prose_errors
@kante.pydantic_input(CreateFixtureInputModel, description="Input for registering a fixture: one binary glTF, in a unit-carrying coordinate system of its own")
class CreateFixtureInput:
    """Input for registering a fixture."""

    name: str | None = strawberry.field(default=None, description="What to call this model. Optional: it is labelled by this or, failing that, by the uploaded file's name")
    store: scalars.GltfLike = strawberry.field(description="The uploaded **binary glTF** (`.glb`). Request it with `requestMediaUpload`, write the file, then `finishMediaUpload`. Its header is read here: a file that is not glTF 2.0 is refused")
    axes: list[PhysicalAxisInput] = strawberry.field(
        description=(
            "The three spatial axes of the model's own space, in the order its vertex components are stored, each with the unit the exporter wrote. Required and never defaulted, as a "
            "collection's axes are. glTF stores (x, y, z) and is metres by specification, so `[x, y, z]` in `meter` is the common answer -- and `millimeter` when the exporter scaled, which "
            "CAD exporters often do. The unit is what makes an IDENTITY registration into a micrometre world come out the right size: it converts by the units, with no factor stated twice"
        )
    )
    folder: strawberry.ID | None = strawberry.field(default=None, description="The folder to file this model in. Organisational only. Defaults to the user's default folder")
    derived_from: list[DerivedFromInput] | None = strawberry.field(
        default=None,
        description="What this model was made from, when anything: a segmentation it was printed from, say. Usually empty -- a coverslip derives from nothing, and it is placed by a registration into a world rather than by lineage",
    )
    provenance_metadata: scalars.Any | None = strawberry.field(default=None, description="How this model was produced: the CAD source, the exporter and its settings")


def _resolve_gltf_store(info: Info, store_id: str) -> "models.MediaStore":
    """The finished media store holding the model's file."""
    store = get_for_org(models.MediaStore, info, id=store_id)
    if not store.populated:
        raise ValueError(f"Media store {store.pk} has not been finished, so the file may still be arriving. Call `finishMediaUpload` once it is written, then register it.")
    return store


def _read_bounds(store: "models.MediaStore") -> dict | None:
    """The model's box, read off its file's JSON chunk. Refuses a file that is not glTF 2.0."""
    datalayer = get_current_datalayer()
    where = f"The uploaded file '{store.original_file_name or store.key}'"
    document = gltf_logic.read_glb_json(lambda start, length: datalayer.read_media_range(store, start, length), where=where)
    return gltf_logic.scene_bounds(document)


def _assert_spatial_axes(axes: list[PhysicalAxisInputModel]) -> None:
    """Refuse anything but three spatial axes: a glTF vertex has exactly three components, all lengths."""
    if len(axes) != 3:
        raise ValueError(f"A fixture's space has exactly three axes, one per vertex component, but {len(axes)} were declared. Declare x, y and z in the order the file stores them.")
    others = [axis.name for axis in axes if str(getattr(axis.type, "value", axis.type)).upper() != "SPACE"]
    if others:
        raise ValueError(f"Every axis of a fixture's space is spatial -- a vertex is a position -- but {others} are not SPACE axes.")


def create_fixture(info: Info, input: CreateFixtureInput) -> types.Fixture:
    """Register a fixture, in a unit-carrying coordinate system of its own.

    Atomic, for `_write_collection`'s reason: the row is written before its axes are checked
    and before its edges are, and a refusal in between must not leave an orphan behind.
    """
    model = input.to_pydantic()
    _assert_spatial_axes(model.axes)

    ctx = CreationContext.from_info(info)
    store = _resolve_gltf_store(info, model.store)
    bounds = _read_bounds(store)

    with transaction.atomic():
        fixture = models.Fixture.objects.create(
            name=model.name,
            store=store,
            bounds=bounds,
            provenance_metadata=model.provenance_metadata or {},
            folder=folder_logic.folder_for_new_container(info, ctx, model.folder, model.derived_from),
            creator=ctx.user,
            organization=ctx.organization,
        )
        # Unit-carrying, unlike a collection's pixel space: the vertices are lengths already.
        system = models.CoordinateSystem.objects.create(name=f"{fixture.label}/model", creator=ctx.user, organization=ctx.organization)
        graph_logic.create_physical_axes(system, model.axes)
        fixture.coordinate_system = system
        fixture.save(update_fields=["coordinate_system"])
        coordinate_system_logic.write_derivation_edges(info, name=fixture.label, own_system=system, derived_from=model.derived_from or [], ctx=ctx)
        # The lens selecting all of it, from the start: what a layer draws through.
        coordinate_system_logic.whole_lens(fixture)
    return fixture


class DeleteFixtureInputModel(BaseModel):
    id: str = Field(description="The ID of the fixture to delete")


@kante.pydantic_input(DeleteFixtureInputModel, description="Input for deleting a fixture by ID")
class DeleteFixtureInput:
    """Input for deleting a fixture by ID."""

    id: strawberry.ID = strawberry.field(description="The ID of the fixture to delete")


delete_fixture = make_delete(models.Fixture, DeleteFixtureInput, owner=self_owner)
