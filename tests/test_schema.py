"""Schema smoke tests: the schema must build and expose the expected
filter inputs and ordering arguments. No database required."""

from mikro_server.schema import schema


def test_schema_builds():
    sdl = schema.as_str()
    assert sdl


def test_filter_inputs_exist():
    sdl = schema.as_str()
    for input_name in [
        "input ArrayDatasetFilter",
        "input FolderFilter",
        "input FileFilter",
        "input AnnotationFilter",
    ]:
        assert input_name in sdl, f"{input_name} missing from schema"


def test_array_datasets_query_has_filters():
    fields = schema.query.__strawberry_definition__.fields
    field = next(f for f in fields if f.name == "array_datasets")
    arg_names = {arg.python_name for arg in field.arguments}
    assert "filters" in arg_names
    assert "ordering" in arg_names


def test_order_inputs_exist():
    sdl = schema.as_str()
    for input_name in [
        "input ArrayDatasetOrder",
        "input FolderOrder",
        "input FileOrder",
        "input AnnotationOrder",
    ]:
        assert input_name in sdl, f"{input_name} missing from schema"


def test_layer_render_graph_surface_exists():
    """The composable in-layer render graph is exposed for input, output and helpers."""
    sdl = schema.as_str()
    for token in [
        "interface LayerRenderNode",
        "type ChannelSourceNode",
        "type BlendNode",
        "type LayerRenderGraph",
        "type LookupStop",
        "input LayerNodeInput",
        "input LayerRenderGraphInput",
        "input LookupStopInput",
        "renderGraph",
        "createRgbLayer",
        "createIntensityLayer",
        "createLabelLayer",
        "type ProjectionNode",
        "enum ProjectionMode",
        "createVolumeLayer",
    ]:
        assert token in sdl, f"{token} missing from schema"


def test_phasor_surface_exists():
    """The phasor node, its read surface and its metadata spokes are exposed.

    An implementation reachable only *through* an interface is dropped from the SDL unless it
    is registered in ``Schema(types=...)`` -- silently, with no error anywhere. The SDL is the
    only place that failure shows, which is what this test is for.
    """
    sdl = schema.as_str()
    for token in [
        "type PhasorNode",
        "type PhasorTransfer",
        "type PhasorCursor",
        "enum PhasorColorMode",
        "enum PhasorCursorKind",
        "type PhasorContext",
        "type PhasorHistogram",
        "type PhasorCalibration",
        "input LayerNodeInput",
        "input PhasorTransferInput",
        "input PhasorCursorInput",
        "createPhasorLayer",
        "createPhasorHistogram",
        "createPhasorCalibration",
    ]:
        assert token in sdl, f"{token} missing from schema"

    assert "SPECTRUM" in sdl, "the SPECTRUM axis type is missing from schema"
    assert "type PhasorNode implements LayerRenderNode" in sdl, "PhasorNode must implement the render-node interface"


def test_polymorphic_layer_subtypes_exist():
    """All the polymorphic Layer subtypes implement the Layer interface."""
    sdl = schema.as_str()
    for token in [
        "type ImageLayer implements Layer",
        "type AnnotationLayer implements Layer",
        "type PointLayer implements Layer",
        "type TrackLayer implements Layer",
        "type MeshLayer implements Layer",
    ]:
        assert token in sdl, f"{token} missing from schema"


def test_layer_alpha_compositing_fields_exist():
    """The polymorphic Layer interface carries the neuroglancer-style alpha compositing knobs."""
    sdl = schema.as_str()
    layer_def = sdl[sdl.find("interface Layer ") : sdl.find("}", sdl.find("interface Layer "))]
    for field in ["blending:", "opacity:", "visible:", "order:"]:
        assert field in layer_def, f"Layer.{field} missing from interface"
    # The ImageLayer carries its data source and the render graph; all rendering
    # (colormap/contrast/gamma) now lives inside the render graph, not on flat fields.
    image_def = sdl[sdl.find("type ImageLayer implements") : sdl.find("}", sdl.find("type ImageLayer implements"))]
    for field in ["lens:", "renderGraph:"]:
        assert field in image_def, f"ImageLayer.{field} missing"
    for field in ["gamma:", "colormap:", "climMin:", "climMax:", "color:"]:
        assert field not in image_def, f"vestigial flat render field ImageLayer.{field} should be gone (moved into the render graph)"
    # The NORMAL (alpha-over) blend mode must be available.
    blending_def = sdl[sdl.find("enum Blending ") : sdl.find("}", sdl.find("enum Blending "))]
    assert "NORMAL" in blending_def


def test_layer_is_polymorphic_interface():
    """Layer is a GraphQL interface implemented by ImageLayer (and future subtypes)."""
    sdl = schema.as_str()
    assert "interface Layer " in sdl
    assert "type ImageLayer implements Layer" in sdl


def test_blending_enums_stay_in_sync():
    """The GraphQL Blending enum values must be a subset of the DB BlendingChoices."""
    from core import enums

    graphql_values = {member.value for member in enums.Blending}
    db_values = set(enums.BlendingChoices.values)
    assert graphql_values <= db_values, graphql_values - db_values


def test_legacy_order_argument_removed():
    sdl = schema.as_str()
    listing = sdl[sdl.find("arrayDatasets(") : sdl.find(")", sdl.find("arrayDatasets("))]
    assert "ordering:" in listing
    assert "order:" not in listing


def test_coordinate_enums_stay_in_sync():
    """Each new GraphQL enum's values must be a subset of the DB TextChoices it is written to.

    The two are separate classes -- strawberry.enum_value corrupts a (str, Enum) --
    so nothing but this test stops them drifting apart, and a drift shows up as a
    Django write of a value the column does not accept.
    """
    from core import enums

    pairs = [
        (enums.TransformKind, enums.TransformKindChoices),
        (enums.CreatableTransformKind, enums.TransformKindChoices),
        (enums.AxisType, enums.AxisTypeChoices),
    ]
    for graphql_enum, db_choices in pairs:
        graphql_values = {member.value for member in graphql_enum}
        db_values = set(db_choices.values)
        assert graphql_values <= db_values, f"{graphql_enum.__name__}: {graphql_values - db_values}"


def test_polymorphic_transformation_subtypes_exist():
    """Every Transformation subtype must implement the interface and be in the SDL.

    A subtype reachable only through the interface is not auto-discovered by
    strawberry: leave it out of the schema's `types=[...]` and it vanishes from the
    SDL with no error at import and none at query time -- the field is simply not
    there. This assertion is the only thing that catches that.
    """
    sdl = schema.as_str()
    for token in [
        "type IdentityTransformation implements Transformation",
        "type ScaleTransformation implements Transformation",
        "type TranslationTransformation implements Transformation",
        "type AffineTransformation implements Transformation",
        "type RotationTransformation implements Transformation",
        "type MapAxisTransformation implements Transformation",
        "type SequenceTransformation implements Transformation",
        "type ByDimensionTransformation implements Transformation",
        "type FieldTransformation implements Transformation",
    ]:
        assert token in sdl, f"{token} missing from schema"


def test_attribute_plan_types_exist():
    """The plan types are computed (not django) types reachable only through one query.

    Nothing else in the schema references them, so a registration slip would drop them
    from the SDL with no error at import and none at query time -- the same failure mode
    the polymorphic-subtype assertion above guards against.
    """
    sdl = schema.as_str()
    for token in [
        "attributePlans(system: ID!, maxDepth: Int",
        "maxJoinDepth: Int",
        "type AttributePlan",
        "interface SampleStep",
        "type LookupStep",
        "type PlanKeyColumn",
        "path: [PlacementStep!]!",
        # The chain: the landing is `hops[0]`, and every later hop crosses one declared
        # reference. `lookup` and `table` left the plan for the hop when the shape went uniform.
        "hops: [Hop!]!",
        "type Hop",
        "type HopVia",
        "enum HopCardinality",
        "keyHeld: String",
        # The two substrates a plan can be rooted in. Reachable only *through* the
        # interface, so an unregistered one vanishes from the SDL silently -- exactly the
        # failure this file exists for.
        "type ArraySample implements SampleStep",
        "type MeshSample implements SampleStep",
        # The third: a network collection whose object ids ride on its wireframe. It was
        # registered in `sample_step_types` but never re-exported from `core.types`, so the
        # SDL had it and the resolver raised AttributeError the first time a network-keyed
        # table was discovered -- which this token alone would not have caught; the
        # query-level test in `tests/test_network_layers.py` does.
        "type NetworkSample implements SampleStep",
        # And the write side that authors a mesh-rooted plan. `KeyedBySourceKind` and its two
        # member inputs were these until 2026-08-20; identification is per-axis and shared with
        # the sparse path now, so the union that authors the edge is `IdentificationInput`.
        "enum IdentificationKind",
        "input DatasetIdentifiesInput",
        "input MeshCollectionIdentifiesInput",
        "input NetworkCollectionIdentifiesInput",
        # The no-edge node member: dropping it from `identification_union_types` erases it
        # from the SDL silently, and per-node tables then become undeclarable.
        "input NetworkCollectionNodesIdentifiesInput",
    ]:
        assert token in sdl, f"{token} missing from schema"
    # A table's column is declared once, on ColumnInput; the second declaration surface
    # (`TableAxisInput` + `CreateTableDatasetInput.axes`) was retired 2026-08-31 because it
    # and `ColumnInput.references` were two wire doors into the one `Column.references` FK.
    assert "TableAxisInput" not in sdl, "the flat declaration must not grow its old sibling back"
    # The statement is derived from `keyColumns` + `attributes` by the worker now
    # (`core/logic/plan_sql.py`); a `sql` field would be a second copy of them, free to drift.
    lookup = sdl[sdl.index("type LookupStep") : sdl.index("}", sdl.index("type LookupStep"))]
    assert "sql:" not in lookup, "LookupStep must not carry the statement"


def test_a_column_reference_is_schema_not_geometry():
    """Table-to-table relations are declared foreign keys, never coordinate-graph edges.

    FIELD is the single crossing from geometry into record-land; between tables, the
    relation does no coordinate work, so it lives on the column. If `references` leaves
    the SDL, the tracking workload is back to client convention.
    """
    sdl = schema.as_str()
    column_def = sdl[sdl.find("type Column ") : sdl.find("}", sdl.find("type Column "))]
    assert "references" in column_def, "Column must carry its declared foreign key"
    table_def = sdl[sdl.find("type TableDataset ") : sdl.find("}", sdl.find("type TableDataset "))]
    assert "referencedBy" in table_def, "TableDataset must answer 'who keys into me'"


def test_no_to_world_resolver():
    """The API ships edges, not resolved paths.

    The same dataset can appear in two scenes under two different registrations, so
    a server-side `toWorld` would be right in one and wrong in the other. If this
    ever fails, someone has added the field that makes the graph a lie.
    """
    sdl = schema.as_str()
    assert "toWorld" not in sdl, "a toWorld resolver was added: the server must not compose paths (see core/models/coords.py)"


def test_nothing_mints_a_world_for_a_dataset():
    """A dataset is staged over a space it is already in, never over a copy of one.

    `createSceneFromDataset` minted a coordinate system whose axes copied the dataset's
    physical space, then authored an identity edge into it -- a third space that was a copy
    of the second, and an edge that existed only to justify the copy. If either name comes
    back, so has the mirror world.

    The word check is the wider net: it catches a *new* mutation reintroducing the idea under
    another name, which the two symbol checks would miss.
    """
    sdl = schema.as_str()
    for token in ["createSceneFromDataset", "CreateSceneFromDatasetInput", "bootstrapScene", "BootstrapSceneInput"]:
        assert token not in sdl, f"`{token}` is back: a dataset is staged over its own space, not over a minted copy of one"

    # The lightpath schema has a real optical mirror (`MirrorElement`, `ElementKind.MIRROR`)
    # and it is not what this is about. What must not come back is the *verb*: a description
    # saying one space mirrors another.
    mirroring = [line.strip() for line in sdl.split("\n") if any(word in line.lower() for word in ("mirroring", "mirrors", "mirrored"))]
    assert mirroring == [], f"the SDL describes one space as mirroring another: {mirroring}"


def test_a_scene_answers_for_its_layers_and_nothing_spatial():
    """A scene is its world plus its layers; every space-level fact hangs off the space.

    Each of these fields used to live on `Scene` and returned an answer identical for
    every scene over the same world -- which is what made them the *space's* facts, not
    the composition's. They now hang off `CoordinateSystem`, reached as
    `worldCoordinateSystem { ... }`. If one reappears on `Scene`, someone has re-derived
    a property of the graph per composition.
    """
    sdl = schema.as_str()
    # Sliced, not searched whole: every one of these tokens also appears legitimately in
    # `type CoordinateSystem` (and `registrations` in `input CreateCoordinateSystemInput`).
    # The trailing space keeps `type Scene ` from matching `type SceneSnapshot`.
    scene_def = sdl[sdl.find("type Scene ") : sdl.find("}", sdl.find("type Scene "))]
    assert "layers" in scene_def, "the slice must be the Scene block: a missed find() would make the assertions below pass on nothing"
    for token in ["registrations", "coordinateSystems", "annotations"]:
        assert token not in scene_def, f"Scene must not answer `{token}`: it is a property of the world, identical for every scene over it"

    system_def = sdl[sdl.find("type CoordinateSystem ") : sdl.find("}", sdl.find("type CoordinateSystem "))]
    for token in ["registrations", "placedSystems", "annotations"]:
        assert token in system_def, f"the space is where `{token}` is read"


def test_layer_carries_no_spatial_fields():
    """Registration is a scene-level edge, not a property of a view of the data."""
    sdl = schema.as_str()
    layer_def = sdl[sdl.find("interface Layer ") : sdl.find("}", sdl.find("interface Layer "))]
    for token in ["affineMatrix", "xDim", "yDim", "zDim", "tDim"]:
        assert token not in layer_def, f"Layer must not carry {token}: two layers over one dataset would carry two copies of one fact"
