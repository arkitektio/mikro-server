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
from asgiref.sync import sync_to_async
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
async def test_deleting_the_shape_keeps_what_was_made_from_it(authenticated_context: HttpContext):
    """The cut goes with the shape; the dataset stays, derived from the collection the shape was in."""
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

    deleted = await schema.execute(DELETE_ANNOTATION, context_value=_fresh_request(ctx), variable_values={"input": {"id": rectangle["id"]}})
    assert not deleted.errors, deleted.errors

    assert await models.LensCut.objects.acount() == 0
    assert await models.ArrayLens.objects.filter(pk=cut["id"]).aexists(), "the lens is the dataset's row, not the shape's"
    result = await schema.execute(DERIVED_FROM, context_value=_fresh_request(ctx), variable_values={"id": cropped_id})
    assert not result.errors, result.errors
    by_lens, by_shape = result.data["arrayDataset"]["derivedFrom"]
    assert by_shape["annotation"] is None and by_shape["output"]["id"] == collection["coordinateSystem"]["id"]
    edges = await sync_to_async(lambda: models.Transformation.objects.filter(annotation__isnull=False).count())()
    assert edges == 0
