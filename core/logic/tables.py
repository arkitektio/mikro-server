"""What core knows about the parquet behind a table store.

Was shared by the legacy ``Table`` GraphQL type and its virtual row/cell resolvers. Those are
gone; what remains is the store-level half, and as of the schema-inference work it is thinner
still: **the DESCRIBE itself moved into the datalayer**, onto ``Datalayer.get_parquet_schema``
beside ``get_zarr_metadata`` and ``get_sparse_metadata``, so a store reads its own schema at
``fill_info`` without core in the loop. ``datalayer`` must never import ``core``
(``tests/test_architecture.py``), and a parquet store asking core how to describe itself would
have been the one exception -- for no reason, since the DuckDB facade it needed never depended
on core either.

So these are now two thin readers over what the store already recorded. Nothing here goes to S3
on the create path any more; by then the file has been described once, at finish, and the answer
is on the row.

That line was briefly crossed the other way too, for a picker that wanted a class column's
distinct values and a measure column's range. It was put back: the client already holds an
``accessGrant`` for the same parquet and is reading it anyway, so a scan here buys nothing it
could not do locally and costs this server a query whose price is set by someone else's row
count.
"""

from datalayer import base_models
from datalayer.datalayer import get_current_datalayer


def parquet_source_for_store(store) -> str:
    """The s3 URL of a parquet store, whichever model references it."""
    return get_current_datalayer().parquet_source(store)


def columns_for_store(store) -> list[base_models.ParquetColumn]:
    """The columns a parquet store's file declares, in file order.

    Reads ``ParquetStore.columns`` -- recorded by ``fill_info`` when the upload was finished --
    and falls back to describing the file for a store written before that field existed. The
    fallback is what makes this safe to call on old rows; it is not a second source of truth,
    because a store that has been finished since never reaches it.
    """
    recorded = getattr(store, "columns", None)
    if recorded is None:
        return get_current_datalayer().get_parquet_schema(store)
    return [base_models.ParquetColumn(**column) for column in recorded]
