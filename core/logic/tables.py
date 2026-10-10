"""What core knows about the parquet behind a table store.

Two thin readers over what the store recorded about itself. The DESCRIBE lives in the
datalayer (``Datalayer.get_parquet_schema``, beside ``get_zarr_metadata`` and
``get_sparse_metadata``) and runs once, at finish, so the answer is on the row and nothing here
goes to S3. ``datalayer`` must never import ``core`` (``tests/test_architecture.py``).

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
