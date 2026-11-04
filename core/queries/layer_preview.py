"""``previewLayer``: the layer a create mutation would make, made and not written.

A viewer stages a layer for a moment -- a crop it has not decided to keep, a result it is
still tuning -- and draws it from exactly what a saved layer would carry. The input is one
of the create mutations' own inputs, so keeping the layer is that same input sent to the
create; there is no preview recipe to drift from the real one. See
:mod:`core.logic.layer_preview` for how the row is built without being saved.
"""

import strawberry
from kante.types import Info

from core import types
from core.logic import scene_graph
from core.logic.layer_preview import previewing
from core.mutations.layer import (
    CreateIntensityLayerInput,
    CreateLabelLayerInput,
    CreateLayerInput,
    CreatePhasorLayerInput,
    CreateRgbLayerInput,
    CreateVolumeLayerInput,
    create_intensity_layer,
    create_label_layer,
    create_layer,
    create_phasor_layer,
    create_rgb_layer,
    create_volume_layer,
)
from core.mutations.mesh_layer import CreateMeshLayerInput, create_mesh_layer
from core.mutations.vector_layer import CreateVectorLayerInput, create_vector_layer

__all__ = ["PreviewLayerInput", "preview_layer"]


@strawberry.input(
    one_of=True,
    description=(
        "Which layer to preview, as the input of the create mutation that would make it: exactly one member. Keeping the preview is that same input sent to "
        "that mutation (`intensity` to `createIntensityLayer`, `mesh` to `createMeshLayer`, ...), which makes the identical layer"
    ),
)
class PreviewLayerInput:
    image: CreateLayerInput | None = strawberry.UNSET
    intensity: CreateIntensityLayerInput | None = strawberry.UNSET
    volume: CreateVolumeLayerInput | None = strawberry.UNSET
    rgb: CreateRgbLayerInput | None = strawberry.UNSET
    label: CreateLabelLayerInput | None = strawberry.UNSET
    phasor: CreatePhasorLayerInput | None = strawberry.UNSET
    vector: CreateVectorLayerInput | None = strawberry.UNSET
    mesh: CreateMeshLayerInput | None = strawberry.UNSET


_CREATES = {
    "image": create_layer,
    "intensity": create_intensity_layer,
    "volume": create_volume_layer,
    "rgb": create_rgb_layer,
    "label": create_label_layer,
    "phasor": create_phasor_layer,
    "vector": create_vector_layer,
    "mesh": create_mesh_layer,
}


def preview_layer(info: Info, input: PreviewLayerInput) -> types.Layer:
    """Run the create the input names in preview mode, and answer with the unsaved row."""
    chosen = [(name, value) for name, value in vars(input).items() if value not in (None, strawberry.UNSET)]
    if len(chosen) != 1:
        raise ValueError(f"Name exactly one layer to preview; got {len(chosen)}: {', '.join(name for name, _ in chosen) or 'none'}.")
    name, value = chosen[0]
    with previewing():
        layer = _CREATES[name](info, value)
    # Its source space must be in the universe its placement is searched over.
    scene_graph.admit_previews(info, layer.scene, [layer])
    return layer
