"""The one question an axis of positions has to answer: what *are* these positions?

An axis is a list of positions and nothing else. To mean anything it has to say what they
are, and there are exactly four ways to answer -- a mask whose pixel values are the ids, a
mesh collection whose geometry carries them, a network collection whose object ids they are,
or a table whose rows the positions are. This module is that answer, shared by the two places
an axis is declared: a sparse matrix's axes, which have nothing *but* this to say, and a
table's INDEX coordinate columns.

**It lives on the axis, which is the whole point.** For a table it used to be split across two
sibling lists -- ``keyedBy`` and the columns' ``references`` -- of which only the second named
an axis at all; the first was matched to its axis by subtraction inside ``write_key_edges``,
which is correct and invisible. Carried on the axis the pairing is the input's own shape, so
"identified exactly once" stops being a check the mutation performs.

**It is a list, because fan-in is real.** One axis may be identified by two masks -- a nucleus
mask and a cell mask keying the same object id -- and ``write_key_edges`` has always written an
edge per entry and refused only *duplicate sources*. The singular form sparse shipped with was
under-modelling, not a guarantee.

Three of the five kinds author a FIELD edge and two do not, and that difference is real
rather than an implementation detail: a mask and a collection are things whose *contents*
identify an object, which is a claim about space and therefore an edge; a table is already in
record-land, where the relation is a foreign key. ``TABLE`` is valid on an **INDEX** axis only,
in either place -- every sparse axis is INDEX by construction, and for a table it is item 7's
product-space case, where an axis's values are already ids and naming the table it enumerates
is what the enumeration is *of*.

Flat-discriminated in the wire shape, following ``core.input_unions`` exactly as
``DerivedFromInput`` does, because GraphQL has no input unions.
"""

from typing import Annotated, ClassVar, Literal

import strawberry
from pydantic import BaseModel, ConfigDict, Field

import kante

from core import enums
from core.input_unions import parse_union_member, prose_errors, union_memberships

_NAME_DESCRIPTION = (
    "What to call the edge this authors, in a graph a person has to read. Defaults to `<source> -> <dataset>`. Only meaningful for the kinds that author one"
)

_AXIS_NAME_DESCRIPTION = (
    "The axis' name, free-form and unique within this dataset -- `bin`, `gene`, `metabolite`, `neuron`. It is the name a colouring names a position along, and the name the "
    "server reports back in `indexableAxes`"
)


class IdentificationInputBase(BaseModel):
    """The fields every identification carries, whichever kind of thing it names."""

    model_config = ConfigDict(extra="forbid")

    kind: enums.IdentificationKind
    name: str | None = None
    validity: enums.PlacementValidity | None = None

    #: The member's own id field, so a reader needs no per-member branch. A ClassVar, so pydantic
    #: treats it as neither a field nor a private attribute.
    SOURCE_FIELD: ClassVar[str] = "source"

    #: Whether this kind authors a FIELD edge. The one behavioural difference between the members,
    #: stated once here rather than branched on at every call site.
    AUTHORS_EDGE: ClassVar[bool] = True

    #: Where a NO-edge identification is written, now that there are two such kinds: on the
    #: column's `references` FK ("table") or its `node_references` FK ("network_nodes").
    #: None for the kinds that author an edge instead. Stated here for `AUTHORS_EDGE`'s
    #: reason -- `split_identifications` routes on it rather than growing a kind branch.
    REFERENCE_TARGET: ClassVar[str | None] = None

    @property
    def source_id(self) -> str:
        """The id of whichever source this member names."""
        return getattr(self, type(self).SOURCE_FIELD)


class DatasetIdentifiesInputModel(IdentificationInputBase):
    """Identified by a label mask, through its intrinsic pixel grid."""

    kind: Literal[enums.IdentificationKind.DATASET] = enums.IdentificationKind.DATASET
    dataset: str
    SOURCE_FIELD: ClassVar[str] = "dataset"


class MeshCollectionIdentifiesInputModel(IdentificationInputBase):
    """Identified by a mesh collection, through its vertex coordinate system."""

    kind: Literal[enums.IdentificationKind.MESH_COLLECTION] = enums.IdentificationKind.MESH_COLLECTION
    mesh_collection: str
    SOURCE_FIELD: ClassVar[str] = "mesh_collection"


class NetworkCollectionIdentifiesInputModel(IdentificationInputBase):
    """Identified by a network collection's **object** ids, through its coordinate system.

    Object ids, never node ids: one position per traced object -- a filament, an arbor, a
    vessel tree -- which is what the object catalog enumerates and what a client that picked a
    wireframe is holding. A node id is unique only within its object, so a per-node
    identification cannot key a row on its own and is the sibling member
    :class:`NetworkCollectionNodesIdentifiesInputModel`, declared beside this one on another axis.
    """

    kind: Literal[enums.IdentificationKind.NETWORK_COLLECTION] = enums.IdentificationKind.NETWORK_COLLECTION
    network_collection: str
    SOURCE_FIELD: ClassVar[str] = "network_collection"


class NetworkCollectionNodesIdentifiesInputModel(IdentificationInputBase):
    """Identified by a network collection's **node** ids, scoped by a sibling object axis.

    ``TABLE``'s sibling, not ``NETWORK_COLLECTION``'s: it authors no edge, because the object
    axis' edge already supplies the one id an edge supplies, and this says what the *other*
    axis enumerates. That is the shape `write_key_edges`' own two-ids refusal points at -- one
    produced axis, every other id axis identified by the table itself -- so the composite key
    lands without touching either of the gates that refuse a two-produce edge.

    One such axis makes the table per-node; two over one collection make it per-edge, read as
    ``(source, target)`` in axis declaration order -- the list's order is already the axis
    order in this input, so order-as-meaning is the established idiom and a role field would
    be a second statement of one fact.
    """

    kind: Literal[enums.IdentificationKind.NETWORK_COLLECTION_NODES] = enums.IdentificationKind.NETWORK_COLLECTION_NODES
    network_collection: str
    SOURCE_FIELD: ClassVar[str] = "network_collection"
    AUTHORS_EDGE: ClassVar[bool] = False
    REFERENCE_TARGET: ClassVar[str | None] = "network_nodes"


class TableIdentifiesInputModel(IdentificationInputBase):
    """Identified by a table whose rows this axis' positions are.

    Authors no edge. That is not an omission: an edge is a claim about how one space maps into
    another, and this claim is a foreign key -- *the values along this axis identify rows of that
    table*. It is also what lets a FIELD edge land on the same dataset at all, since an axis
    identified this way is one the edge is not expected to supply.
    """

    kind: Literal[enums.IdentificationKind.TABLE] = enums.IdentificationKind.TABLE
    table: str
    SOURCE_FIELD: ClassVar[str] = "table"
    AUTHORS_EDGE: ClassVar[bool] = False
    REFERENCE_TARGET: ClassVar[str | None] = "table"


#: Every identification kind, keyed by discriminator value.
IDENTIFICATION_MEMBERS: dict[str, type[BaseModel]] = {
    enums.IdentificationKind.DATASET.value: DatasetIdentifiesInputModel,
    enums.IdentificationKind.MESH_COLLECTION.value: MeshCollectionIdentifiesInputModel,
    enums.IdentificationKind.NETWORK_COLLECTION.value: NetworkCollectionIdentifiesInputModel,
    enums.IdentificationKind.NETWORK_COLLECTION_NODES.value: NetworkCollectionNodesIdentifiesInputModel,
    enums.IdentificationKind.TABLE.value: TableIdentifiesInputModel,
}

#: The union the pydantic side carries, so the mutation never sees the flat wire shape.
IdentificationSpec = Annotated[
    DatasetIdentifiesInputModel
    | MeshCollectionIdentifiesInputModel
    | NetworkCollectionIdentifiesInputModel
    | NetworkCollectionNodesIdentifiesInputModel
    | TableIdentifiesInputModel,
    Field(discriminator="kind"),
]

#: The wire fields carrying a source id, one per member.
_SOURCE_FIELDS = ("dataset", "mesh_collection", "network_collection", "table")


@prose_errors
@strawberry.input(
    description=(
        "What a column's values or an axis' positions **are**, as a discriminated union: `kind` selects which sort of thing is being named, and only that member's id field is "
        "read -- any other is rejected. Carried by a sparse dataset's axes and by a table's columns alike -- the one spelling of every 'values here identify things there' claim. "
        "`DATASET`, `MESH_COLLECTION` and `NETWORK_COLLECTION` author a FIELD edge from the source into this data, which is also what makes it reachable from a layer over that "
        "source (INDEX axes only -- the edge produces an axis); `TABLE` authors no edge and states a foreign key instead, on an INDEX axis or a plain data column; "
        "`NETWORK_COLLECTION_NODES` likewise authors none, on an INDEX axis scoped by a sibling object axis"
    ),
)
class IdentificationInput:
    """How one column or axis is identified, discriminated by `kind`.

    Deliberately not pydantic-backed: the wire type is flat because GraphQL has no input unions,
    and ``to_pydantic`` is where that flatness is corrected into the strict member.
    """

    kind: enums.IdentificationKind = strawberry.field(description="Which sort of thing identifies this column or axis. It fixes which id field below is read; any other is rejected")
    dataset: strawberry.ID | None = strawberry.field(
        default=None,
        description="(DATASET) The label dataset whose pixel values are the positions along this axis. Its own pixel grid is both the edge's input and its field, which is what a label mask is",
    )
    mesh_collection: strawberry.ID | None = strawberry.field(
        default=None,
        description="(MESH_COLLECTION) The mesh collection whose geometry rows carry the positions. Its vertex space is both the edge's input and its field, exactly as a mask's grid is",
    )
    network_collection: strawberry.ID | None = strawberry.field(
        default=None,
        description=(
            "(NETWORK_COLLECTION) The network collection whose **object** ids are the positions along this axis -- one per traced object, never per node; its coordinate system is "
            "both the edge's input and its field, exactly as a mesh collection's is. (NETWORK_COLLECTION_NODES) The collection whose **node** ids they are instead -- no edge, scoped "
            "by a sibling INDEX axis keyed by the same collection's objects; two such axes over one collection are its edges' (source, target), in axis declaration order. One field "
            "for both kinds because the id names the same sort of thing and `kind` already says which claim is being made"
        ),
    )
    table: strawberry.ID | None = strawberry.field(
        default=None,
        description=(
            "(TABLE) The table whose rows this column's values are -- an INDEX axis' enumeration, or a plain data column's foreign key (an `instance_id` referencing a table of "
            "tracks: the edge of the join graph `colorBys` walks). Must be keyed by exactly one INDEX coordinate column, which is where a value is looked up -- the contract "
            "`Column.references` records. A matrix with 19 059 features costs one picker entry because of this, not 19 059"
        ),
    )
    name: str | None = strawberry.field(default=None, description=_NAME_DESCRIPTION)
    validity: enums.PlacementValidity | None = strawberry.field(default=None, description="How far the edge this authors may be trusted. Only meaningful for the kinds that author one")

    def to_pydantic(self) -> BaseModel:
        """Match the flat wire fields to the member model `kind` selects, strictly."""
        supplied = {name: getattr(self, name) for name in ("kind", "name", "validity", *_SOURCE_FIELDS)}
        data = {name: value for name, value in supplied.items() if value is not None}
        return parse_union_member(IDENTIFICATION_MEMBERS, data, noun="identification")


def _identification_member(model: type, key: "enums.IdentificationKind", description: str):  # noqa: ANN202 - a decorator factory
    """Publish one member input of the IdentificationInput union."""
    return kante.pydantic_input(
        model,
        directives=union_memberships("IdentificationInput", key=key.value),
        description=f"{description}. Published for codegen; the wire type is the flat IdentificationInput",
    )


@_identification_member(DatasetIdentifiesInputModel, enums.IdentificationKind.DATASET, "The fields a DATASET identification reads")
class DatasetIdentifiesInput:
    """The DATASET member of the identification union."""

    kind: enums.IdentificationKind = strawberry.field(description="The discriminator: which member of IdentificationInput this is")
    dataset: strawberry.ID = strawberry.field(description="The label dataset whose pixel values are the positions along this axis")
    name: str | None = strawberry.field(default=None, description=_NAME_DESCRIPTION)
    validity: enums.PlacementValidity | None = strawberry.field(default=None, description="How far the edge this authors may be trusted")


@_identification_member(MeshCollectionIdentifiesInputModel, enums.IdentificationKind.MESH_COLLECTION, "The fields a MESH_COLLECTION identification reads")
class MeshCollectionIdentifiesInput:
    """The MESH_COLLECTION member of the identification union."""

    kind: enums.IdentificationKind = strawberry.field(description="The discriminator: which member of IdentificationInput this is")
    mesh_collection: strawberry.ID = strawberry.field(description="The mesh collection whose geometry rows carry the positions")
    name: str | None = strawberry.field(default=None, description=_NAME_DESCRIPTION)
    validity: enums.PlacementValidity | None = strawberry.field(default=None, description="How far the edge this authors may be trusted")


@_identification_member(NetworkCollectionIdentifiesInputModel, enums.IdentificationKind.NETWORK_COLLECTION, "The fields a NETWORK_COLLECTION identification reads")
class NetworkCollectionIdentifiesInput:
    """The NETWORK_COLLECTION member of the identification union."""

    kind: enums.IdentificationKind = strawberry.field(description="The discriminator: which member of IdentificationInput this is")
    network_collection: strawberry.ID = strawberry.field(description="The network collection whose object ids are the positions along this axis -- one per traced object, never per node")
    name: str | None = strawberry.field(default=None, description=_NAME_DESCRIPTION)
    validity: enums.PlacementValidity | None = strawberry.field(default=None, description="How far the edge this authors may be trusted")


@_identification_member(NetworkCollectionNodesIdentifiesInputModel, enums.IdentificationKind.NETWORK_COLLECTION_NODES, "The fields a NETWORK_COLLECTION_NODES identification reads")
class NetworkCollectionNodesIdentifiesInput:
    """The NETWORK_COLLECTION_NODES member of the identification union."""

    kind: enums.IdentificationKind = strawberry.field(description="The discriminator: which member of IdentificationInput this is")
    network_collection: strawberry.ID = strawberry.field(
        description="The network collection whose NODE ids the positions along this axis are, scoped by a sibling INDEX axis keyed by the same collection's object ids. Authors no edge. Two such axes over one collection are its edges' (source, target), in axis declaration order"
    )


@_identification_member(TableIdentifiesInputModel, enums.IdentificationKind.TABLE, "The fields a TABLE identification reads")
class TableIdentifiesInput:
    """The TABLE member of the identification union."""

    kind: enums.IdentificationKind = strawberry.field(description="The discriminator: which member of IdentificationInput this is")
    table: strawberry.ID = strawberry.field(description="The table whose rows this axis' positions are, keyed by its single INDEX coordinate column")


#: The member inputs published to the SDL, for the schema's ``types=[...]``. Dropping one erases
#: it from the SDL silently, and the union then advertises a member nobody can construct.
identification_union_types: list[type] = [
    DatasetIdentifiesInput,
    MeshCollectionIdentifiesInput,
    NetworkCollectionIdentifiesInput,
    NetworkCollectionNodesIdentifiesInput,
    TableIdentifiesInput,
]
