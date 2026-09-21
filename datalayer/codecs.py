"""Which Parquet compression codecs the viewers this server serves can decode, and refusing the rest.

The server stores whatever arrives. Until now it also validated no codec at all -- its only
read of an uploaded Parquet was a DuckDB ``DESCRIBE`` of the footer, and a collection's parts
were never opened. So a file compressed with something the viewer cannot decode uploaded
cleanly, described cleanly, and then rendered as nothing, with no error anywhere to attach the
cause to. This contract used to live in the Python client, which was the wrong side: the
frontend a deployment serves is the server's to know, not every client's.

Two readers, two sets, because they are different code paths in the frontend:

* **A table** is read with DuckDB-WASM (``read_parquet()`` over s3 httpfs). DuckDB decodes
  everything Parquet defines except LZO, so :data:`TABLE_CODECS` is wide and this check will
  rarely fire. Its standing value is that the readable set is written down in one place.
* **A mesh or network collection's parts** are read with hyparquet, and ``hyparquet-compressors``
  is not installed there: the viewer registers ZSTD by hand out of ``fzstd`` and nothing else,
  and hyparquet's own built-ins are UNCOMPRESSED and SNAPPY. So :data:`PART_CODECS` has three
  members, and GZIP, BROTLI and LZ4 -- all selectable in the writers' ``ParquetCompression`` --
  would produce a collection that silently does not draw. That is where this check has teeth.

Names are DuckDB's, as ``parquet_metadata()`` reports them, which is also how pyarrow's footer
spells them. Only the footer is read, never a row.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

__all__ = [
    "PART_CODECS",
    "PART_READER",
    "TABLE_CODECS",
    "TABLE_READER",
    "refuse_unreadable_codecs",
]

#: What a table's reader decodes: DuckDB-WASM. Everything Parquet defines except LZO. Both LZ4
#: spellings are here because a footer reports ``LZ4`` for ``lz4`` and ``lz4_raw`` alike.
TABLE_CODECS: Final[frozenset[str]] = frozenset({"UNCOMPRESSED", "SNAPPY", "GZIP", "ZSTD", "BROTLI", "LZ4", "LZ4_RAW"})

#: What a collection part's reader decodes: hyparquet's two built-ins plus the ZSTD the viewer
#: registers out of ``fzstd``. Narrow because ``hyparquet-compressors`` is not a dependency there.
PART_CODECS: Final[frozenset[str]] = frozenset({"UNCOMPRESSED", "SNAPPY", "ZSTD"})

TABLE_READER: Final[str] = "the table viewer, which reads Parquet with DuckDB-WASM"
PART_READER: Final[str] = "the scene viewer, which reads collection parts with hyparquet and has no decoder for anything else"


def refuse_unreadable_codecs(found: Iterable[str], readable: Iterable[str], *, where: str, reader: str) -> None:
    """Refuse a Parquet whose compression the reader downstream cannot decode.

    Raised at the finish of an upload, because the alternative is finding out from a view that
    loaded, described and then displayed nothing. The message reaches the client verbatim as
    ``errors[0].message``, so it says what to write with instead.

    Args:
        found: Every codec named in the file's footer, one per column chunk, as DuckDB spells them.
        readable: The codecs the reader decodes.
        where: What is being refused, for the message -- a location a person can go and look at.
        reader: Which viewer cannot read it, for the message.

    Raises:
        ValueError: If any found codec is outside the readable set.
    """
    readable_set = frozenset(name.upper() for name in readable)
    unreadable = sorted({name.upper() for name in found} - readable_set)
    if not unreadable:
        return
    raise ValueError(
        f"{where} is compressed with {', '.join(unreadable)}, which {reader} cannot decode. It reads {', '.join(sorted(readable_set))}. "
        "The upload itself succeeded and the file describes cleanly, so without this refusal the failure would surface as a view that renders nothing. "
        "Write the file with ZSTD, which every reader here decodes, and finish the upload again."
    )
