"""The DuckDB statement a lookup step describes -- built from the plan, never carried by it.

A plan names *facts*: which parquet, which key columns bind which held values, which columns
come back. The statement is derived from those facts and nothing else, so it used to ride on
the plan as a fourth field -- and was thereby a second copy of the other three, free to drift,
and the one field a non-duckdb consumer ignored. It lives here now, beside whoever executes
it, as one function over the structured step.

**Standard library only, on purpose.** Nothing in here imports Django, strawberry or this
package: the module is copied unchanged into the ``mikro_next`` client, and a test pins that
it can be. It reads a lookup step in any of the three shapes it arrives in -- the builder's
``LookupSpec`` dataclass, the strawberry ``LookupStep`` object, or the plain camelCase dict a
GraphQL client holds -- through two accessors that know nothing but attribute and key names.

Bind order, which every executor must honour: **the parquet path or URL first** (the
``read_parquet(?)`` argument, supplied by the worker from its own access grant so credentials
and locations never appear in a plan), **then the key values in ``keyColumns`` order**.
Identifiers come from validated declared columns and are quoted; values are ``?`` placeholders
and are never interpolated.

Cardinality says how the key values are bound. ``ONE`` binds each as a scalar and returns the
attributes -- the hover shape, one id in hand. ``MANY`` binds each as a *list* and returns the
key columns as well, because a row fetched for several values at once has to say which of them
it answers, or the next hop has nothing to bind from. It is a floor, not a guarantee: a ONE
lookup can still return several rows ((t, i) uniqueness is a convention no index backs), and a
client that collected several parents may execute a ONE step as MANY.
"""

from typing import Any, Iterable, Literal

Cardinality = Literal["ONE", "MANY"]


def quote_identifier(name: str) -> str:
    """Quote a column name as a SQL identifier, doubling embedded quotes.

    The one thing standing between a stored column name and the statement, so it is a named
    function with its own test rather than an inline expression. Values never pass through here.
    """
    return '"' + name.replace('"', '""') + '"'


def _get(thing: Any, *names: str) -> Any:
    """The first of ``names`` present on ``thing``, as an attribute or a mapping key."""
    for name in names:
        if isinstance(thing, dict):
            if name in thing:
                return thing[name]
        elif hasattr(thing, name):
            return getattr(thing, name)
    raise KeyError(f"none of {names} on {type(thing).__name__}")


def _names(columns: Iterable[Any]) -> list[str]:
    """Column names, whether each entry is a column or a key binding wrapping one."""
    names = []
    for entry in columns:
        try:
            column = _get(entry, "column")
        except KeyError:
            column = entry
        names.append(str(_get(column, "name")))
    return names


def key_column_names(lookup: Any) -> list[str]:
    """The parquet columns the key values bind, in bind order."""
    return _names(_get(lookup, "key_columns", "keyColumns"))


def attribute_names(lookup: Any) -> list[str]:
    """The columns the statement returns beyond the keys."""
    return _names(_get(lookup, "attributes"))


def build_lookup_sql(lookup: Any, *, cardinality: Cardinality = "ONE") -> str:
    """The parameterized DuckDB statement for one TABLE lookup step.

    ``ONE``: ``SELECT <attributes> FROM read_parquet(?) WHERE "k" = ? [AND ...]``. A table whose
    every column is a key has nothing else to report, so the SELECT falls back to the keys --
    the worker still learns the row exists.

    ``MANY``: ``SELECT <keys>, <attributes> FROM read_parquet(?) WHERE "k" IN (SELECT unnest(?)) [AND ...]``,
    each ``?`` after the path bound as a list. The keys are always selected here, so every row
    carries the value it was fetched for.

    Never ``SELECT *``: the plan says exactly what comes back.
    """
    keys = key_column_names(lookup)
    if not keys:
        raise ValueError("a lookup binds at least one key column; a statement with no WHERE would read every row")
    attributes = attribute_names(lookup)

    if cardinality == "MANY":
        selected = keys + [name for name in attributes if name not in keys]
        where = " AND ".join(f"{quote_identifier(name)} IN (SELECT unnest(?))" for name in keys)
    elif cardinality == "ONE":
        selected = attributes or keys
        where = " AND ".join(f"{quote_identifier(name)} = ?" for name in keys)
    else:
        raise ValueError(f"cardinality is ONE or MANY, not {cardinality!r}")

    select_list = ", ".join(quote_identifier(name) for name in selected)
    return f"SELECT {select_list} FROM read_parquet(?) WHERE {where}"
