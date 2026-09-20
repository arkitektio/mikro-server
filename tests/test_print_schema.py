"""Smoke test: the GraphQL schema must build and render to a non-empty SDL string.

No database required — this only imports and stringifies the schema.
"""

from mikro_server.schema import schema


def test_print_schema():
    sdl = str(schema)
    print(sdl)  # visible with `pytest -s`
    assert sdl.strip(), "Schema SDL should not be empty"


def test_embedding_columns_stay_out_of_the_schema():
    """The vector columns are storage, not API: no type or input may expose them."""
    sdl = str(schema)
    assert "embedding" not in sdl.lower()
