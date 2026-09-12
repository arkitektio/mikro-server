"""Custom database fields.

Currently one: a minimal mapping of PostgreSQL's ``cube`` extension type, which
stores an axis-aligned N-dimensional box and is GiST-indexable -- overlap
(``&&``), containment (``@>``) and nearest-by-distance (``<->``) all use the
index. Everything is exchanged as cube *text literals* with an explicit
``::cube`` cast: psycopg3's binding mode must never be relied on to cast an
untyped text parameter implicitly.
"""

from django.db import models


def cube_literal(low, high=None) -> str:
    """The cube text literal for a point or a (low, high) box, e.g. ``(0.0, 1.0),(2.0, 3.0)``."""

    def corner(values) -> str:
        return "(" + ", ".join(repr(float(value)) for value in values) + ")"

    return corner(low) if high is None else f"{corner(low)},{corner(high)}"


class CubeField(models.Field):
    """A PostgreSQL ``cube`` column: an axis-aligned box, GiST-indexable for spatial search.

    Write-only as far as the API is concerned: values are denormalized from a JSON
    source of truth at write time and only ever *queried* (never resolved back), so
    reads return the raw text representation and no parsing is implemented.
    """

    description = "An axis-aligned N-dimensional box (PostgreSQL cube)"

    def db_type(self, connection) -> str:
        """The column type: the extension's ``cube``."""
        return "cube"

    def get_placeholder(self, value, compiler, connection) -> str:
        """The write placeholder, with an explicit cast.

        A bare text parameter is `text`, not `unknown`, under client- and
        server-side binding alike, and `text -> cube` has no implicit cast.
        """
        return "%s::cube"

    def get_prep_value(self, value) -> str | None:
        """Serialize a bbox dict, (low, high) pair, or bare point to a cube text literal."""
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, dict):
            # The intrinsic_bbox dict itself: {'min': [...], 'max': [...]}.
            return cube_literal(value["min"], value["max"])
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
            return cube_literal(value[0], value[1])
        return cube_literal(value)

    def from_db_value(self, value, expression, connection) -> str | None:
        """The raw cube text representation -- never parsed, the column is query-only."""
        return value


class CubeOverlaps(models.Lookup):
    """``&&`` -- whether two boxes share any point. GiST-accelerated."""

    lookup_name = "overlaps"

    def as_sql(self, compiler, connection) -> tuple[str, list]:
        """``lhs && rhs::cube``."""
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} && {rhs}::cube", lhs_params + rhs_params


class CubeContainsPoint(models.Lookup):
    """``@>`` -- whether the box contains the probe (a zero-volume point cube). GiST-accelerated."""

    lookup_name = "contains_point"

    def as_sql(self, compiler, connection) -> tuple[str, list]:
        """``lhs @> rhs::cube``."""
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} @> {rhs}::cube", lhs_params + rhs_params


CubeField.register_lookup(CubeOverlaps)
CubeField.register_lookup(CubeContainsPoint)
