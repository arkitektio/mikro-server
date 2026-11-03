"""A shape can be asked what was made from it.

Two different things are made from an annotation, and they are recorded in two places
because they are two kinds of thing:

- a **lens cut to it** (`createLens(annotation:)`) writes no pixels and is derived from
  nothing -- it is the dataset's row for the voxels under the shape's box, shared by whoever
  else selects them. So the cut is a row beside the lens (`LensCut`), read as
  `Annotation.derivedLenses`.
- **data computed from it** -- a crop written out, an image straightened along a path --
  has a `derivedFrom`, and names the shape there as an ANNOTATION source: an edge into the
  collection's drawing space that carries the annotation, read from the other end as
  `Annotation.derivedResidents`.

Both are a record of the act, not a relation in space: the shape is mutable and what was
made from it is not.
"""

import pytest
from kante.context import HttpContext

from core import models
from mikro_server.schema import schema
from tests import seed
from tests.test_condensed_placement import _fresh_request
from tests.test_derived_datasets import _derive
from tests.test_lens_geometry import CREATE_LENS, _collection_on, _draw, _lens, _lens_from

LINEAGE = """
query Lineage($id: ID!) {
  annotation(id: $id) {
    id
    derivedLenses { id shape }
    derivedResidents { __typename ... on ArrayDataset { id name } }
  }
}
"""

DERIVED_FROM = """
query DerivedFrom($id: ID!) {
  arrayDataset(id: $id) {
    derivedFrom { kind annotation { id } output { id } }
  }
}
"""

DELETE_ANNOTATION = "mutation D($input: DeleteAnnotationInput!) { deleteAnnotation(input: $input) }"

CORNERS = [[1.0, 2.0], [1.0, 6.0], [4.0, 6.0], [4.0, 2.0]]


async def _lineage(ctx: HttpContext, annotation_id: str) -> dict:
    result = await schema.execute(LINEAGE, context_value=_fresh_request(ctx), variable_values={"id": annotation_id})
    assert not result.errors, result.errors
    return result.data["annotation"]


async def _drawn_on(ctx: HttpContext, name: str = "Flat") -> tuple[models.ArrayDataset, dict]:
    """A (y, x) dataset and a collection drawn straight on its grid."""
    dataset = await seed.create_array_dataset(ctx, name, axes=seed.YX_AXES, shapes=[[6, 8]])
    collection = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    return dataset, collection


# --- a lens cut to a shape ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_lens_cut_to_a_shape_is_listed_on_it_once(authenticated_context: HttpContext):
    """Cutting records the cut; cutting again is the same cut, and the same lens."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    elsewhere = await _draw(ctx, collection["id"], "POINT", [[5.0, 3.0]])

    assert (await _lineage(ctx, rectangle["id"]))["derivedLenses"] == [], "a shape nobody cut to has made nothing"

    cut = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(cut, dict), cut
    again = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(again, dict) and again["id"] == cut["id"]

    assert [lens["id"] for lens in (await _lineage(ctx, rectangle["id"]))["derivedLenses"]] == [cut["id"]]
    assert (await _lineage(ctx, elsewhere["id"]))["derivedLenses"] == [], "the cut is the rectangle's, not its collection's"
    assert await models.LensCut.objects.acount() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_cut_is_beside_the_lens_because_the_lens_is_shared(authenticated_context: HttpContext):
    """Typed slices reach the same row and record no cut; a second shape with the same box records its own."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    twin = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)

    typed = await _lens(ctx, dataset, [{"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}])
    assert (await _lineage(ctx, rectangle["id"]))["derivedLenses"] == [], "selecting the same voxels by hand cuts to no shape"

    cut = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(cut, dict) and cut["id"] == typed["id"], "one row per selection, however it was asked for"
    other = await _lens_from(ctx, dataset, annotation=twin["id"])
    assert isinstance(other, dict) and other["id"] == cut["id"]

    assert [lens["id"] for lens in (await _lineage(ctx, rectangle["id"]))["derivedLenses"]] == [cut["id"]]
    assert [lens["id"] for lens in (await _lineage(ctx, twin["id"]))["derivedLenses"]] == [cut["id"]], "a column on the lens could have named only one of them"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_cut_within_a_lens_is_that_lens_under_the_box(authenticated_context: HttpContext):
    """The crop a viewer asks for: the shape it clicked, on the channel its layer draws. One lens is made, and it is the one recorded."""
    ctx = authenticated_context
    dataset = await seed.create_array_dataset(ctx, "Channels", shapes=[[3, 6, 8]])
    collection = await _collection_on(ctx, axes=["c", "y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}})
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    channel = await _lens(ctx, dataset, [{"axis": "c", "start": 1, "stop": 2}])
    before = await models.ArrayLens.objects.acount()

    result = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"lens": channel["id"], "annotation": rectangle["id"]}})
    assert not result.errors, result.errors
    cut = result.data["createLens"]

    assert cut["shape"] == [1, 4, 5], "the channel the lens pins stays pinned"
    by_hand = await _lens(ctx, dataset, [{"axis": "c", "start": 1, "stop": 2}, {"axis": "y", "start": 1, "stop": 5}, {"axis": "x", "start": 2, "stop": 7}])
    assert by_hand["id"] == cut["id"], "it is the dataset's row for those voxels"
    assert await models.ArrayLens.objects.acount() == before + 1, "no all-channel box lens is made on the way"
    assert [lens["id"] for lens in (await _lineage(ctx, rectangle["id"]))["derivedLenses"]] == [cut["id"]], "the lens handed back is the one the cut names"

    whole = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"lens": str((await seed.create_lens(ctx, dataset, slices=[])).pk), "annotation": rectangle["id"]}})
    assert not whole.errors, whole.errors
    over_dataset = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(over_dataset, dict) and whole.data["createLens"]["id"] == over_dataset["id"], "within the whole lens is over the dataset"

    rows = await _lens(ctx, dataset, [{"axis": "y", "start": 5, "stop": 6}])
    missed = await schema.execute(CREATE_LENS, context_value=_fresh_request(ctx), variable_values={"input": {"lens": rows["id"], "annotation": rectangle["id"]}})
    assert missed.errors and "outside the lens along 'y'" in missed.errors[0].message

    boxed = await schema.execute(
        CREATE_LENS,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"lens": channel["id"], "box": {"coordinateSystem": collection["coordinateSystem"]["id"], "windows": [{"axis": "y", "min": 1, "max": 4}]}}},
    )
    assert boxed.errors and "`box` names its own frame" in boxed.errors[0].message, "a bare box is still cut from the dataset"


# --- data computed from a shape -------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_dataset_names_the_shape_it_was_computed_from(authenticated_context: HttpContext):
    """A crop written out: the lens places it, the shape is why it is what it is, and the shape can list it."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    sibling = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    cut = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(cut, dict), cut

    made = await _derive(
        ctx,
        "Cropped",
        axes=seed.YX_AXES,
        shape=[4, 5],
        entries=[
            {"kind": "LENS", "lens": cut["id"], "transform": {"kind": "IDENTITY"}, "valueRelation": "IDENTICAL"},
            {"kind": "ANNOTATION", "annotation": rectangle["id"]},
        ],
    )
    assert not made.errors, made.errors
    cropped_id = made.data["createArrayDataset"]["id"]

    result = await schema.execute(DERIVED_FROM, context_value=_fresh_request(ctx), variable_values={"id": cropped_id})
    assert not result.errors, result.errors
    by_lens, by_shape = result.data["arrayDataset"]["derivedFrom"]
    assert by_lens["kind"] == "IDENTITY" and by_lens["annotation"] is None, "the edge that places the data is about no shape"
    assert by_shape["kind"] == "UNMAPPABLE", "naming a shape claims no geometry"
    assert by_shape["annotation"] == {"id": rectangle["id"]}
    assert by_shape["output"]["id"] == collection["coordinateSystem"]["id"], "it lands where a collection derivation lands: the drawing space"

    assert (await _lineage(ctx, rectangle["id"]))["derivedResidents"] == [{"__typename": "ArrayDataset", "id": cropped_id, "name": "Cropped"}]
    assert (await _lineage(ctx, sibling["id"]))["derivedResidents"] == [], "the collection is shared, the lineage is the shape's"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_shapes_of_one_collection_are_two_sources(authenticated_context: HttpContext):
    """They land in one space, and are not a duplicate: the shape is part of what was named."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    first = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    second = await _draw(ctx, collection["id"], "POINT", [[5.0, 3.0]])
    lens = await seed.create_lens(ctx, dataset, slices=[])

    made = await _derive(
        ctx,
        "Between",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "ANNOTATION", "annotation": first["id"]},
            {"kind": "ANNOTATION", "annotation": second["id"]},
        ],
    )
    assert not made.errors, made.errors
    between = made.data["createArrayDataset"]["id"]
    assert [resident["id"] for resident in (await _lineage(ctx, first["id"]))["derivedResidents"]] == [between]
    assert [resident["id"] for resident in (await _lineage(ctx, second["id"]))["derivedResidents"]] == [between]

    twice = await _derive(
        ctx,
        "Twice",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "ANNOTATION", "annotation": first["id"]},
            {"kind": "ANNOTATION", "annotation": first["id"]},
        ],
    )
    assert twice.errors and "distinct source" in twice.errors[0].message, "one shape twice is one source twice"

    beside_its_collection = await _derive(
        ctx,
        "Both",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
            {"kind": "ANNOTATION", "annotation": first["id"]},
            {"kind": "ANNOTATION_COLLECTION", "annotationCollection": collection["id"]},
        ],
    )
    assert beside_its_collection.errors and "distinct source" in beside_its_collection.errors[0].message, "the shape already says the collection"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_named_first_cannot_hide_the_lens_that_places_the_data(authenticated_context: HttpContext):
    """An untransformed shape is UNMAPPABLE, and the primary parent may not be while a mappable one follows."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    lens = await seed.create_lens(ctx, dataset, slices=[])

    refused = await _derive(
        ctx,
        "Backwards",
        axes=seed.YX_AXES,
        shape=[6, 8],
        entries=[
            {"kind": "ANNOTATION", "annotation": rectangle["id"]},
            {"kind": "LENS", "lens": str(lens.pk), "transform": {"kind": "IDENTITY"}},
        ],
    )
    assert refused.errors and "primary parent" in refused.errors[0].message

    stray_field = await _derive(ctx, "Stray", axes=seed.YX_AXES, shape=[6, 8], entries=[{"kind": "ANNOTATION", "annotation": rectangle["id"], "lens": str(lens.pk)}])
    assert stray_field.errors, "a field outside the chosen member is rejected, never dropped"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_shape_goes_once_what_was_computed_within_it_has(authenticated_context: HttpContext):
    """Refused while the crop stands (deletes follow lineage); once it is gone, the cut goes with the shape and the lens stays."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    cut = await _lens_from(ctx, dataset, annotation=rectangle["id"])
    assert isinstance(cut, dict), cut
    made = await _derive(
        ctx,
        "Cropped",
        axes=seed.YX_AXES,
        shape=[4, 5],
        entries=[{"kind": "LENS", "lens": cut["id"], "transform": {"kind": "IDENTITY"}}, {"kind": "ANNOTATION", "annotation": rectangle["id"]}],
    )
    assert not made.errors, made.errors
    cropped_id = made.data["createArrayDataset"]["id"]

    refused = await schema.execute(DELETE_ANNOTATION, context_value=_fresh_request(ctx), variable_values={"input": {"id": rectangle["id"]}})
    assert refused.errors and "dataset 'Cropped'" in refused.errors[0].message, "the crop would lose the shape it was cut along"

    gone = await schema.execute(
        "mutation D($input: DeleteArrayDatasetInput!) { deleteArrayDataset(input: $input) }", context_value=_fresh_request(ctx), variable_values={"input": {"id": cropped_id}}
    )
    assert not gone.errors, gone.errors
    deleted = await schema.execute(DELETE_ANNOTATION, context_value=_fresh_request(ctx), variable_values={"input": {"id": rectangle["id"]}})
    assert not deleted.errors, deleted.errors

    assert await models.LensCut.objects.acount() == 0
    assert await models.ArrayLens.objects.filter(pk=cut["id"]).aexists(), "the lens is the dataset's row, not the shape's"


# --- a shape made from a shape --------------------------------------------------------

DRAW_FROM = """
mutation Draw($input: CreateAnnotationInput!) {
  createAnnotation(input: $input) { id kind }
}
"""

DRAW_MANY_FROM = """
mutation DrawMany($input: CreateAnnotationsInput!) {
  createAnnotations(input: $input) { id kind }
}
"""

SHAPE_LINEAGE = """
query ShapeLineage($id: ID!) {
  annotation(id: $id) {
    id
    derivedFrom { relation reason source { id } child { id } }
    derivedAnnotations { id }
  }
}
"""

PATH = [[1.0, 1.0], [2.0, 3.0], [3.0, 2.0], [5.0, 5.0]]
SMOOTHER = [[1.0, 1.0], [2.5, 2.5], [5.0, 5.0]]


async def _draw_from(ctx: HttpContext, collection_id: str, kind: str, vectors: list[list[float]], derived_from: list[dict]):  # noqa: ANN202 - strawberry's ExecutionResult
    return await schema.execute(
        DRAW_FROM,
        context_value=_fresh_request(ctx),
        variable_values={"input": {"collection": collection_id, "kind": kind, "vectors": vectors, "derivedFrom": derived_from}},
    )


async def _shape_lineage(ctx: HttpContext, annotation_id: str) -> dict:
    result = await schema.execute(SHAPE_LINEAGE, context_value=_fresh_request(ctx), variable_values={"id": annotation_id})
    assert not result.errors, result.errors
    return result.data["annotation"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_refined_path_names_the_path_it_refines_and_writes_no_edge(authenticated_context: HttpContext):
    """Shape -> shape is a link row: the collection's own lineage, and the coordinate graph, are untouched."""
    ctx = authenticated_context
    _, collection = await _drawn_on(ctx)
    rough = await _draw(ctx, collection["id"], "PATH", PATH)
    edges_before = await models.Transformation.objects.acount()

    made = await _draw_from(ctx, collection["id"], "PATH", SMOOTHER, [{"annotation": rough["id"], "relation": "REFINED", "reason": "smoothed"}])
    assert not made.errors, made.errors
    smooth = made.data["createAnnotation"]

    assert await models.Transformation.objects.acount() == edges_before, "a shape made from a shape is no edge in space"
    assert (await _shape_lineage(ctx, smooth["id"]))["derivedFrom"] == [{"relation": "REFINED", "reason": "smoothed", "source": {"id": rough["id"]}, "child": {"id": smooth["id"]}}]
    assert (await _shape_lineage(ctx, rough["id"]))["derivedAnnotations"] == [{"id": smooth["id"]}]
    assert (await _shape_lineage(ctx, rough["id"]))["derivedFrom"] == [], "the source was drawn from nothing"
    assert (await _lineage(ctx, rough["id"]))["derivedResidents"] == [], "a shape is not a container: derivedResidents stays the data computed from it"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_converted_changes_the_kind_and_refined_keeps_it(authenticated_context: HttpContext):
    """The relation is checked against the two kinds, across collections as within one."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    other = await _collection_on(ctx, axes=["y", "x"], derived_from={"kind": "DATASET", "dataset": str(dataset.pk), "transform": {"kind": "IDENTITY"}}, name="Converted")
    rectangle = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)

    as_polygon = await _draw_from(ctx, other["id"], "POLYGON", CORNERS, [{"annotation": rectangle["id"], "relation": "CONVERTED"}])
    assert not as_polygon.errors, as_polygon.errors
    assert (await _shape_lineage(ctx, rectangle["id"]))["derivedAnnotations"] == [{"id": as_polygon.data["createAnnotation"]["id"]}], "a source in another collection is still a source"

    converted_to_itself = await _draw_from(ctx, collection["id"], "RECTANGLE", CORNERS, [{"annotation": rectangle["id"], "relation": "CONVERTED"}])
    assert converted_to_itself.errors and "REFINED" in converted_to_itself.errors[0].message

    refined_into_another_kind = await _draw_from(ctx, collection["id"], "POLYGON", CORNERS, [{"annotation": rectangle["id"], "relation": "REFINED"}])
    assert refined_into_another_kind.errors and "CONVERTED" in refined_into_another_kind.errors[0].message

    assert await models.Annotation.objects.acount() == 2, "a refused source leaves nothing drawn"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_merge_names_every_source_in_order(authenticated_context: HttpContext):
    """MERGED is several sources or none; a source named twice is refused."""
    ctx = authenticated_context
    _, collection = await _drawn_on(ctx)
    left = await _draw(ctx, collection["id"], "POLYGON", CORNERS)
    right = await _draw(ctx, collection["id"], "POLYGON", [[4.0, 2.0], [4.0, 6.0], [6.0, 6.0], [6.0, 2.0]])

    alone = await _draw_from(ctx, collection["id"], "POLYGON", CORNERS, [{"annotation": left["id"], "relation": "MERGED"}])
    assert alone.errors and "at least two" in alone.errors[0].message

    mixed = await _draw_from(ctx, collection["id"], "POLYGON", CORNERS, [{"annotation": left["id"], "relation": "MERGED"}, {"annotation": right["id"], "relation": "REFINED"}])
    assert mixed.errors and "each as MERGED" in mixed.errors[0].message

    twice = await _draw_from(ctx, collection["id"], "POLYGON", CORNERS, [{"annotation": left["id"], "relation": "MERGED"}, {"annotation": left["id"], "relation": "MERGED"}])
    assert twice.errors and "more than once" in twice.errors[0].message

    merged = await _draw_from(ctx, collection["id"], "POLYGON", CORNERS, [{"annotation": right["id"], "relation": "MERGED"}, {"annotation": left["id"], "relation": "MERGED"}])
    assert not merged.errors, merged.errors
    sources = [link["source"]["id"] for link in (await _shape_lineage(ctx, merged.data["createAnnotation"]["id"]))["derivedFrom"]]
    assert sources == [right["id"], left["id"]], "the creator's order, not the sources' age"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_bulk_draw_links_each_shape_after_the_insert(authenticated_context: HttpContext):
    """createAnnotations: the pieces of a split each name the source, and a refused one draws nothing."""
    ctx = authenticated_context
    _, collection = await _drawn_on(ctx)
    whole = await _draw(ctx, collection["id"], "POLYGON", CORNERS)
    split = [{"annotation": whole["id"], "relation": "SPLIT"}]

    result = await schema.execute(
        DRAW_MANY_FROM,
        context_value=_fresh_request(ctx),
        variable_values={
            "input": {
                "collection": collection["id"],
                "annotations": [
                    {"kind": "POLYGON", "vectors": [[1.0, 2.0], [1.0, 4.0], [4.0, 4.0]], "derivedFrom": split},
                    {"kind": "POLYGON", "vectors": [[1.0, 4.0], [1.0, 6.0], [4.0, 6.0]], "derivedFrom": split},
                    {"kind": "POINT", "vectors": [[5.0, 3.0]]},
                ],
            }
        },
    )
    assert not result.errors, result.errors
    pieces = [shape["id"] for shape in result.data["createAnnotations"]]
    assert [shape["id"] for shape in (await _shape_lineage(ctx, whole["id"]))["derivedAnnotations"]] == pieces[:2]
    assert (await _shape_lineage(ctx, pieces[2]))["derivedFrom"] == []

    before = await models.Annotation.objects.acount()
    refused = await schema.execute(
        DRAW_MANY_FROM,
        context_value=_fresh_request(ctx),
        variable_values={
            "input": {
                "collection": collection["id"],
                "annotations": [
                    {"kind": "POLYGON", "vectors": CORNERS, "derivedFrom": split},
                    {"kind": "POLYGON", "vectors": CORNERS, "derivedFrom": [{"annotation": whole["id"], "relation": "CONVERTED"}]},
                ],
            }
        },
    )
    assert refused.errors and "A CONVERTED shape" in refused.errors[0].message, refused.errors
    assert await models.Annotation.objects.acount() == before, "nothing of a refused batch is drawn"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_source_of_another_organization_is_not_there(authenticated_context: HttpContext, other_org_context: HttpContext):
    """Nothing of another organization resolves as a source."""
    _, theirs = await _drawn_on(other_org_context, "Theirs")
    their_path = await _draw(other_org_context, theirs["id"], "PATH", PATH)
    _, mine = await _drawn_on(authenticated_context, "Mine")

    refused = await _draw_from(authenticated_context, mine["id"], "PATH", SMOOTHER, [{"annotation": their_path["id"], "relation": "REFINED"}])
    assert refused.errors and "does not exist or is not visible" in refused.errors[0].message
    malformed = await _draw_from(authenticated_context, mine["id"], "PATH", SMOOTHER, [{"annotation": "not-a-uuid", "relation": "REFINED"}])
    assert malformed.errors and "does not exist or is not visible" in malformed.errors[0].message


SHAPE_WALK = """
query Walk($start: LineageRootInput!, $up: Int!, $down: Int!) {
  lineage(start: $start, up: $up, down: $down) {
    nodes { key kind id name depth hasMoreUp hasMoreDown }
    edges { key kind source target annotationId annotationRelation primary }
  }
}
"""


async def _walk_shape(ctx: HttpContext, kind: str, id: str, *, up: int, down: int) -> dict:
    result = await schema.execute(SHAPE_WALK, context_value=_fresh_request(ctx), variable_values={"start": {"kind": kind, "id": id}, "up": up, "down": down})
    assert not result.errors, result.errors
    return result.data["lineage"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_lineage_walk_starts_at_a_shape(authenticated_context: HttpContext):
    """Rooted at a shape: up to what it was made from, down to the shapes made from it and the data computed within them."""
    ctx = authenticated_context
    dataset, collection = await _drawn_on(ctx)
    rough = await _draw(ctx, collection["id"], "RECTANGLE", CORNERS)
    made = await _draw_from(ctx, collection["id"], "RECTANGLE", [[1.0, 2.0], [4.0, 6.0]], [{"annotation": rough["id"], "relation": "REFINED"}])
    assert not made.errors, made.errors
    snapped = made.data["createAnnotation"]["id"]
    cut = await _lens_from(ctx, dataset, annotation=snapped)
    assert isinstance(cut, dict), cut
    cropped = await _derive(
        ctx,
        "Cropped",
        axes=seed.YX_AXES,
        shape=[4, 5],
        entries=[{"kind": "LENS", "lens": cut["id"], "transform": {"kind": "IDENTITY"}}, {"kind": "ANNOTATION", "annotation": snapped}],
    )
    assert not cropped.errors, cropped.errors
    cropped_id = cropped.data["createArrayDataset"]["id"]

    down = await _walk_shape(ctx, "ANNOTATION", rough["id"], up=1, down=2)
    depths = {node["key"]: node["depth"] for node in down["nodes"]}
    assert depths == {f"annotation:{rough['id']}": 0, f"annotation:{snapped}": 1, f"dataset:{cropped_id}": 2}, "the collection's other residents are not the shape's lineage"
    by_target = {edge["target"]: edge for edge in down["edges"]}
    assert by_target[f"annotation:{snapped}"]["kind"] == "SHAPE_DERIVATION"
    assert by_target[f"annotation:{snapped}"]["annotationRelation"] == "REFINED"
    crop_edge = by_target[f"dataset:{cropped_id}"]
    assert crop_edge["kind"] == "DERIVATION" and crop_edge["source"] == f"annotation:{snapped}" and crop_edge["annotationId"] == snapped
    assert crop_edge["primary"] is False, "a shape is why the crop is what it is, not what places it"
    crop_node = next(node for node in down["nodes"] if node["key"] == f"dataset:{cropped_id}")
    assert crop_node["hasMoreUp"], "the crop has parents this walk left out: its dataset, through the lens"

    up = await _walk_shape(ctx, "ANNOTATION", snapped, up=1, down=0)
    assert {node["key"] for node in up["nodes"]} == {f"annotation:{snapped}", f"annotation:{rough['id']}"}

    from_crop = await _walk_shape(ctx, "DATASET", cropped_id, up=1, down=0)
    assert all(node["kind"] != "ANNOTATION" for node in from_crop["nodes"]), "walking up from data still lands on containers; the edge names the shape"
    assert any(edge["annotationId"] == snapped for edge in from_crop["edges"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_organization_cannot_walk_from_a_shape(authenticated_context: HttpContext, other_org_context: HttpContext):
    """The root is scoped like any node."""
    _, collection = await _drawn_on(authenticated_context)
    rectangle = await _draw(authenticated_context, collection["id"], "RECTANGLE", CORNERS)
    result = await schema.execute(SHAPE_WALK, context_value=_fresh_request(other_org_context), variable_values={"start": {"kind": "ANNOTATION", "id": rectangle["id"]}, "up": 1, "down": 1})
    assert result.errors
