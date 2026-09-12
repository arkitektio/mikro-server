"""Reaching a column that is one or more tables away from the one the ids land in.

A ``FIELD`` edge is the single crossing from geometry into record-land: it lands on one table
of per-object rows, and the coordinate graph stops there -- *"tables are always leaves"*
(:mod:`core.logic.attribute_plans`). What relates one table to another is not an edge but a
**schema fact**, ``Column.references``: a declared foreign key saying that this column's
values identify rows of that table. RFC-7 put it on the schema for exactly that reason, and
listed following it server-side under its non-goals, *"additive later"*.

This is that addition, and deliberately the narrow form of it. A join path is **stored,
validated and offered** by the server; it is still **executed by the client**, one lookup at a
time, exactly as the single hop already is (``docs/attribute-plans-api.md``). Since 2026-09-02
``attributePlans`` carries the same chain as ``hops`` (:mod:`core.logic.join_walk`), and a
table-to-table hop reports this very ``joinPath`` so a stored entry finds the hop that resolves
it. The non-goal RFC-7 named was server-side lookup *chaining*, and nothing here -- or there --
chains anything server-side.

The path a step describes: at each hop the client holds a value, looks it up in the step's table
by that table's single INDEX coordinate column, reads ``column``, and carries that value to the
next table. An empty path is the direct case -- the value column lives in the table the FIELD
edge landed on -- which is what every colouring written before this existed means.
"""

from pydantic import BaseModel


class JoinStepModel(BaseModel):
    """One reference hop: the column whose values identify rows of the next table.

    Carries the table it *stands in*, not the one it points at, so that a step is checkable on
    its own terms -- the target is already named by the next step (or, for the last one, by the
    entry's own ``table``), and stating it twice would be two copies of one fact free to drift.

    It deliberately does **not** name the target's key column. Which column holds the target's
    row identity is declared on the target -- its single INDEX coordinate column, which
    ``createTableDataset`` refuses a reference target without -- and a per-step copy could
    disagree with the table it copies. The same argument :class:`~core.render.color_by.ColorByModel`
    already makes about naming the table rather than the join.
    """

    table: str
    column: str
