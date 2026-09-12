"""Splitting a set of declared identifications into the two things they become.

A column says what its values are, and the answer lands in one of two places depending on
which of them it is (:mod:`core.inputs.identification` argues why the split is real):

* a **mask** or a **collection** identifies by having contents that *are* the ids, which is a
  claim about space, so it authors a FIELD edge from that source's system into this one --
  and only an *axis* can be keyed that way, since the edge produces an axis. Which axis is
  legal is the derivation's question (:func:`core.logic.coordinate_system` refuses a keyed
  axis the source shares, or one it cannot supply, with prose that names the source);
* a **table** identifies by its rows being the values, which is a foreign key, so it authors
  no edge and is written on the column itself (``references``). Legal on an INDEX axis (the
  product-space case) and on a plain data column -- an ``instance_id`` referencing a table of
  tracks, which is what the retired ``ColumnInput.references`` used to spell;
* a **network collection's nodes** identify the same no-edge way -- the sibling object axis'
  edge already supplies the one id an edge supplies -- and land on the column's
  ``node_references`` instead, on an INDEX axis only.

One function, because the two creates were about to grow two copies of it -- and because the
resolution a table identification needs (:func:`resolve_reference_target`: the target must be
dereferenceable, one INDEX coordinate column, not synthetic row enumeration) is the part it
would be easiest to write once and forget once.

Deliberately *not* here: "is every axis identified?". That is true of a sparse matrix by
construction and false of a table -- a localization table's `x` axis is identified by nothing
and should be -- so it belongs to the caller that means it.
"""

from typing import Sequence

from kante.types import Info

from core import enums, models


def split_identifications(
    info: Info,
    *,
    name: str,
    entries: Sequence[tuple[str, Sequence[object]]],
    axis_types: "dict[str, enums.AxisType | None] | None" = None,
) -> tuple[dict[str, "models.TableDataset"], dict[str, "models.NetworkCollection"], list[tuple[str, object]]]:
    """Resolve the no-edge references, and collect the identifications that author edges.

    Args:
        info: The request, for org-scoped lookups.
        name: What to call the thing being created, in an error.
        entries: ``(column or axis name, identifications)``, in declaration order -- every
            column of a table now, not only its axes, since a data column's table reference
            travels through the same door. The identifications are the lowered union
            members, so each carries ``AUTHORS_EDGE``, ``REFERENCE_TARGET`` and
            ``source_id``.
        axis_types: Each entry's declared axis type -- an AxisType for an axis column,
            ``None`` for a data column -- or ``None`` for the whole mapping when every entry
            is an INDEX axis by construction (a sparse matrix). What a kind is allowed on
            follows from it: edge-authoring kinds need an axis (the derivation judges which);
            NODES needs INDEX; TABLE needs INDEX or a data column; SPACE/TIME refuse the
            no-edge kinds, their values being positions.

    Returns:
        ``(references, node_references, keyed)`` -- the resolved table per TABLE-identified
        column, the resolved network collection per NODES-identified axis, and one
        ``(name, identification)`` per edge-authoring source, in declaration order.
        An axis identified by two masks appears twice in ``keyed``, which is what fan-in is.
    """
    references: dict[str, "models.TableDataset"] = {}
    node_references: dict[str, "models.NetworkCollection"] = {}
    keyed: list[tuple[str, object]] = []

    for column_name, identifications in entries:
        axis_type = axis_types.get(column_name) if axis_types is not None else enums.AxisType.INDEX
        for identification in identifications:
            if identification.AUTHORS_EDGE:
                # A keying source's FIELD edge PRODUCES an axis of this space, so it can
                # only land on an axis. Which axis is another question with better answers
                # downstream: the derivation refuses one the source shares ("passes through
                # rather than being supplied") or cannot supply, naming the source -- so a
                # wrong axis *type* is left to it rather than refused generically here.
                if axis_type is None:
                    raise ValueError(
                        f"Column '{column_name}' of '{name}' is keyed by a source that authors a FIELD edge, but it is a data column, which is not an axis of this table's "
                        "space, so there is no axis for the edge to produce. A keyed column is an INDEX axis -- declare it `axisType: INDEX` if its values really are that "
                        "source's ids."
                    )
                keyed.append((column_name, identification))
                continue

            # A coordinate column's values ARE its coordinates -- they place the row in this
            # space. For a SPACE or TIME axis, claiming they simultaneously identify rows
            # elsewhere would make the axis two different maps at once: a position in
            # nanometres and a row id are different things, and which one a reader follows
            # would be convention. An INDEX axis is the opposite case, and not arbitrarily:
            # its values are *already* ids, so naming the table -- or the collection's nodes
            # -- it enumerates is not a second map, it is what the enumeration is of. That is
            # what makes a product space expressible -- a contact map indexed by
            # (nucleus, cell) has two coordinate axes because a row is the pair, and one
            # pixel holds one value, so a mask supplies only one of them. The same sentence
            # covers a node table: a row is (object, node), the object axis' edge supplies
            # the object, and this axis says whose nodes the rest are. A DATA column may
            # carry a TABLE reference too -- an `instance_id` referencing a table of tracks
            # is a foreign key, not a map -- but not a NODES one, whose composite scoping is
            # an axis convention.
            if axis_type is not None and axis_type != enums.AxisType.INDEX:
                what = "a table" if identification.REFERENCE_TARGET == "table" else "a network collection's nodes"
                raise ValueError(
                    f"Axis '{column_name}' of '{name}' is identified by {what}, but it is a {axis_type.value} axis: its values are positions rather than ids, so they place the "
                    "row in this space and cannot also identify rows elsewhere. Put the reference on a data column -- or, if its values really are ids, declare the column "
                    "`axisType: INDEX`, which is an enumeration and may say what it enumerates."
                )
            if axis_type is None and identification.REFERENCE_TARGET == "network_nodes":
                raise ValueError(
                    f"Column '{column_name}' of '{name}' is identified by a network collection's nodes but is not an axis. A node id is scoped by the sibling object axis, which "
                    "is an axis convention -- declare the column `axisType: INDEX` so the (object, node) pair is the row's key."
                )

            if column_name in references or column_name in node_references:
                # One column is one enumeration, whichever kind answered first. Two answers --
                # two tables, a table and a collection's nodes -- would be two different
                # claims about what a value in it is, which is not a richer statement,
                # it is an ambiguous one.
                raise ValueError(
                    f"Column '{column_name}' of '{name}' is identified by more than one enumeration. A column enumerates one thing: two answers to what a value in it is would "
                    "be ambiguous, not richer. Fan-in is only meaningful for the kinds that author an edge -- two masks may key one axis, because each edge stands on its own."
                )

            if identification.REFERENCE_TARGET == "network_nodes":
                from core.scoping import get_for_org

                node_references[column_name] = get_for_org(models.NetworkCollection, info, id=identification.source_id)
            else:
                references[column_name] = resolve_reference_target(info, identification.source_id, f"Column '{column_name}'")

    return references, node_references, keyed


def resolve_reference_target(info: Info, target_id: str, label: str) -> "models.TableDataset":
    """The table an identification names, checked to be one an id can be looked up in.

    Re-exported from :mod:`core.mutations.table_dataset`, where it lived while only that
    module needed it. Imported lazily for the reason every `core.logic` module imports
    mutations lazily: the mutation imports this one at module level.
    """
    from core.mutations.table_dataset import resolve_reference_target as resolve

    return resolve(info, target_id, label)
