"""Custom GraphQL scalars for the datalayer app.

The NewTypes are used in annotations; the GraphQL definitions in
:data:`SCALAR_MAP` are merged into the schema's ``StrawberryConfig.scalar_map``
in ``mikro_server/schema.py``.
"""

from typing import NewType

import strawberry
from strawberry.types.scalar import ScalarDefinition

#: A count of bytes. Its own scalar because GraphQL's ``Int`` is 32-bit and a store passes
#: 2 GiB routinely; serialized as a plain JSON number (exact up to 2^53 -- 8 PiB).
ByteCount = NewType("ByteCount", int)


def _parse_byte_count(v: object) -> int:
    """Accept an int, or a numeric string from a client that cannot hold one."""
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise ValueError(f"ByteCount must be an integer, got {v!r}")
    count = int(v)
    if count < 0:
        raise ValueError(f"ByteCount cannot be negative, got {count}")
    return count


SCALAR_MAP: dict[object, ScalarDefinition] = {
    ByteCount: strawberry.scalar(
        name="ByteCount",
        description="A number of bytes. 64-bit, unlike Int: serialized as a JSON number, and accepted as a number or a numeric string.",
        serialize=int,
        parse_value=_parse_byte_count,
    ),
}
