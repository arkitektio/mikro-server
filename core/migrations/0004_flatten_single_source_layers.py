"""Flatten the image layers whose render graph said nothing a column could not.

Every `kind=IMAGE` row whose graph is a blend of exactly one source becomes the kind that
source is: an INTENSITY layer, or a PHASOR one, or an INTENSITY layer with a
`projection_mode` when the source sits under a projection. That is the whole of it, and the
restraint is the point -- three refusals are as load-bearing as the rewrite:

**A three-child red/green/blue blend stays IMAGE.** It is exactly the shape
`core.enums.BootstrapLayerKind.RGB` says is a three-marker fluorescence acquisition far more
often than a photograph, and the two cannot be told apart by shape -- which is why RGB is
never inferred. The `label="rgb"` string the old builders stamped on the root is not evidence
either: `label` is client-writable on every node (`core.render.layer.inputs`), and
`create_intensity_layer` stamped `label="intensity"` the same way, so they are captions. A
caption match would silently fuse somebody's three markers into one picture where none of
them could be hidden or reordered again. These rows keep rendering pixel-identically as
general image layers; only newly created RGB layers get the flat kind.

**A graph carrying anything a flat kind has no column for stays IMAGE.** An authored transfer
curve (`stops`), an inverted mapping, a solid tint, a per-channel opacity, a root blend that
is not additive: none of them survives the move, so a row carrying one is not flattened
rather than flattened lossily. The flat kinds carry exactly what their builder mutations
took as input, and that is what makes this migration safe to run without reading each row.

**History rows are left alone.** `Layer` carries a `ProvenanceField`, so there is a
`historicallayer` table, and a past state of a layer genuinely *was* an image layer with a
graph. Rewriting it would falsify the record to make it agree with the present, which is the
opposite of what the record is for.
"""

from django.db import migrations

#: Values a flat intensity layer has no column for. A transfer setting any of them is a
#: transfer the flat kind cannot express, so its layer stays an IMAGE.
_UNFLATTENABLE = ("stops", "invert", "color")


def _channel_of(node: dict) -> "dict | None":
    """The one channel source this node is, or None if it is anything else."""
    return node if node.get("kind") == "channel" else None


def _flattenable_transfer(transfer: dict) -> bool:
    """Whether every value in this transfer has a column on an intensity layer to land in."""
    if any(transfer.get(field) for field in _UNFLATTENABLE):
        return False
    opacity = transfer.get("opacity")
    # Per-channel opacity inside a one-channel layer is expressible as the layer's own alpha
    # only when it is 1.0 -- that is, only when it says nothing. Anything else is a second
    # alpha the flat kind has nowhere to put, and multiplying it into `Layer.opacity` would
    # be this migration inventing a value the author never wrote.
    return opacity is None or opacity == 1.0


def _flatten(graph: dict) -> "dict | None":
    """The flat columns this render graph is equivalent to, or None if it is not equivalent to any.

    Returns a dict of field values to write on the row, `kind` included. None means leave the
    row exactly as it is -- the honest answer for every graph that genuinely composites, and
    for every one whose values would not survive the move.
    """
    root = (graph or {}).get("root") or {}
    if root.get("kind") != "blend":
        return None
    children = root.get("children") or []
    if len(children) != 1:
        return None

    child = children[0]
    projection_mode = None
    if child.get("kind") == "projection":
        grandchildren = child.get("children") or []
        if len(grandchildren) != 1:
            return None
        projection_mode = child.get("mode")
        child = grandchildren[0]

    if child.get("kind") == "phasor":
        # A phasor under a projection is a projected phasor, and a PHASOR layer has no column
        # for that. Rare to the point of hypothetical, and still not a reason to drop it.
        if projection_mode is not None:
            return None
        return {
            "kind": "phasor",
            "name": child.get("label") or root.get("label"),
            "phasor_render": {
                "phasor_axis": child.get("phasor_axis"),
                "intensity_axis": child.get("intensity_axis"),
                "intensity_index": child.get("intensity_index") or 0,
                "harmonic": child.get("harmonic") or 1,
                "transfer": child.get("transfer") or {},
            },
            "render_graph": None,
        }

    channel = _channel_of(child)
    if channel is None:
        return None

    # ADDITIVE is what every builder wrote and what blending one child means anyway; a root
    # that says something else was authored by hand and is saying it about a composite that
    # is not here. Refuse rather than silently drop the mode.
    if (root.get("blending") or "additive") != "additive":
        return None

    transfer = channel.get("transfer") or {}
    if not _flattenable_transfer(transfer):
        return None

    return {
        "kind": "intensity",
        # The channel's own label first: it is the one the bootstrap wrote "DAPI" into. The
        # root's is the fallback, which is where the single-channel builders put theirs.
        "name": channel.get("label") or root.get("label"),
        "intensity_axis": channel.get("intensity_axis"),
        "intensity_index": channel.get("intensity_index") or 0,
        "colormap": transfer.get("colormap"),
        "clim_min": transfer.get("clim_min"),
        "clim_max": transfer.get("clim_max"),
        "gamma": transfer.get("gamma"),
        "projection_mode": projection_mode,
        "render_graph": None,
    }


def flatten_layers(apps, schema_editor):
    """Rewrite every image layer whose graph is a blend of one source into its own flat kind."""
    Layer = apps.get_model("core", "Layer")

    flattened, kept = 0, 0
    for layer in Layer.objects.filter(kind="image").exclude(render_graph=None).iterator():
        fields = _flatten(layer.render_graph)
        if fields is None:
            kept += 1
            continue
        for field, value in fields.items():
            setattr(layer, field, value)
        # `name` is only ever filled from a label the graph carried. A row whose graph had
        # none keeps its null rather than being given a generated one -- "channel 0" invented
        # here would be indistinguishable from one the bootstrap deliberately wrote.
        layer.save(update_fields=list(fields))
        flattened += 1

    if kept:
        print(f"  flattened {flattened} image layers; left {kept} as IMAGE (they composite, carry a curve/tint/opacity a flat kind has no column for, or are three-channel blends that cannot be told from RGB)")


def unflatten_layers(apps, schema_editor):
    """Rebuild a render graph from the flat columns, so the migration is reversible.

    Not the inverse of `flatten_layers` over the whole table, and cannot be: a layer created
    *after* this migration as an INTENSITY or PHASOR layer never had a graph, and this gives
    it the one it would have had. That is the right behaviour for a downgrade -- the older
    code can only read graphs -- but it means running this backwards and forwards is not a
    round trip for rows created in between. It never is for a migration that adds a kind.
    """
    Layer = apps.get_model("core", "Layer")

    for layer in Layer.objects.filter(kind__in=("intensity", "phasor")).iterator():
        if layer.kind == "phasor":
            render = layer.phasor_render or {}
            child = {
                "kind": "phasor",
                "label": layer.name,
                "visible": True,
                "phasor_axis": render.get("phasor_axis"),
                "intensity_axis": render.get("intensity_axis"),
                "intensity_index": render.get("intensity_index") or 0,
                "harmonic": render.get("harmonic") or 1,
                "transfer": render.get("transfer") or {},
            }
            blending = "normal"
        else:
            child = {
                "kind": "channel",
                "label": layer.name,
                "visible": True,
                "intensity_axis": layer.intensity_axis,
                "intensity_index": layer.intensity_index or 0,
                "transfer": {
                    "clim_min": layer.clim_min,
                    "clim_max": layer.clim_max,
                    "colormap": layer.colormap,
                    "gamma": layer.gamma,
                    "opacity": 1.0,
                    "invert": False,
                },
            }
            if layer.projection_mode:
                child = {"kind": "projection", "mode": layer.projection_mode, "label": "projection", "children": [child]}
            blending = "additive"

        layer.kind = "image"
        layer.render_graph = {"root": {"kind": "blend", "blending": blending, "label": layer.name, "children": [child]}}
        layer.phasor_render = None
        layer.save(update_fields=["kind", "render_graph", "phasor_render"])


class Migration(migrations.Migration):
    dependencies = [("core", "0003_flat_layer_kinds")]

    operations = [migrations.RunPython(flatten_layers, unflatten_layers)]
