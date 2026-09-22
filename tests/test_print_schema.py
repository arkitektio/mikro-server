"""Smoke test: the GraphQL schema must build and render to a non-empty SDL string.

No database required — this only imports and stringifies the schema.
"""

from mikro_server.schema import schema


def test_print_schema():
    sdl = str(schema)
    print(sdl)  # visible with `pytest -s`
    assert sdl.strip(), "Schema SDL should not be empty"


#: The types that publish their stored vector as an `Embedding` string.
EMBEDDING_TYPES = {"ArrayDataset", "Folder", "TableDataset"}


def _fields_named_embedding(sdl: str) -> dict:
    """``{"type"|"input": {names of the definitions carrying an `embedding` field}}``."""
    import re

    found = {"type": set(), "input": set()}
    kind = name = None
    for line in sdl.splitlines():
        header = re.match(r"(type|input|interface)\s+(\w+)", line)
        if header:
            kind, name = header.group(1), header.group(2)
        elif line.startswith("}"):
            kind = name = None
        elif kind in found and re.match(r"\s+embedding(\(|:)", line):
            found[kind].add(name)
    return found


def test_only_the_embedded_types_publish_a_vector():
    """A vector is readable, and only where a model actually carries one.

    This used to assert the word "embedding" appeared nowhere in the SDL. The vectors are
    published now -- as one self-describing `Embedding` string per row -- so the blanket ban
    is gone, but the half of it that mattered is not: the exact set of types is pinned, so a
    vector column cannot arrive on a type by accident.
    """
    assert _fields_named_embedding(str(schema))["type"] == EMBEDDING_TYPES


def test_no_input_accepts_an_embedding():
    """Read-only: a client may not write a vector, or the healer's contract is a fiction."""
    assert _fields_named_embedding(str(schema))["input"] == set()


def test_the_raw_model_column_is_never_published():
    """`embeddingModel` is carried inside the `Embedding` string, never as its own field."""
    assert "embeddingModel" not in str(schema)

