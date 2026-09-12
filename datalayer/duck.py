"""A DuckDB connection configured against the datalayer's S3 credentials.

**Lives here because it never depended on core.** It was written in ``core/duck.py`` and imported
nothing from the domain apps -- only settings, boto3 and duckdb -- which is the description of a
storage backend. The move is what lets ``ParquetStore.fill_info`` read its own schema: a store
that has to ask ``core`` how to describe itself would invert the one dependency rule this
codebase enforces with a test (``tests/test_architecture.py``), and the rule is right -- every
other store reads its own metadata without leaving this app.

The queries themselves stay out of here. This class owns the *connection* -- the S3 secret and
nothing else -- while what to ask a parquet lives on :class:`~datalayer.datalayer.Datalayer`
beside ``get_zarr_metadata`` and ``get_sparse_metadata``, so all three store kinds answer the
same question in the same place.
"""

from contextvars import ContextVar
from urllib.parse import urlparse
from functools import cached_property
import boto3
from django.conf import settings
from strawberry.extensions import SchemaExtension
import duckdb



current_duckdb: ContextVar = ContextVar("duckdb", default=None)


class DuckLayer:

    @cached_property
    def connection(self) -> boto3.Session:
        """Get a boto3 session for S3 without s3v4 signature"""

        # The endpoint comes from the configured datalayer, not a literal: the host moved from
        # `minio` to `rustfs` and a hardcoded name resolves to nothing, so every parquet read
        # failed the HEAD with a DNS error. `netloc` (not `hostname`) because DuckDB's ENDPOINT
        # wants host:port bare -- no scheme, and dropping the port sends it to :80. USE_SSL is
        # read off the parsed scheme rather than `settings.AWS_S3_USE_SSL`, which is hardcoded
        # True while the datalayer speaks plain http.
        parsed = urlparse(settings.AWS_S3_ENDPOINT_URL)
        use_ssl = "true" if parsed.scheme == "https" else "false"

        secret_query = f"""
        CREATE SECRET secret1 (
            TYPE S3,
            KEY_ID '{settings.AWS_ACCESS_KEY_ID}',
            SECRET '{settings.AWS_SECRET_ACCESS_KEY}',
            REGION '{settings.AWS_S3_REGION_NAME}',
            ENDPOINT '{parsed.netloc}',
            USE_SSL {use_ssl},
            URL_STYLE 'path'
        );
        """

        x = duckdb.connect()
        x.execute(secret_query)
        return x

    def sql(self, query: str):
        """Run a query against the S3-configured connection and return the relation.

        The facade `columns_for_store` was already written against -- it called `duck.sql(...)`
        on this class, which only ever offered `connection`, so every parquet schema validation
        raised `AttributeError` before reaching DuckDB. Delegating here rather than reaching
        through `.connection` at the call site keeps the secret setup an implementation detail
        of this class, which is the only reason it exists.
        """
        return self.connection.sql(query)

    def with_table(self, table, table_name: str = "table1"):

        self.connection.execute(f"CREATE TABLE {table_name} (a INTEGER, b VARCHAR);")
        return self


def get_current_duck() -> DuckLayer:
    return DuckLayer()


class DuckExtension(SchemaExtension):

    def on_operation(self):
        t1 = current_duckdb.set(DuckLayer())

        yield
        current_duckdb.reset(t1)
