"""What a container weighs, as SQL expressions over the stores that hold it.

A store measures its own bytes when its upload is finished (`size_bytes`, null when it could
not); a container is the sum of its stores. Expressions rather than properties so a list can
order by them and a page reads them in the query that loads it.
"""

from django.db.models import BigIntegerField, Expression, OuterRef, Subquery, Sum

from core import models


def array_dataset_size_bytes() -> Expression:
    """The bytes of an array dataset: every level of its pyramid, as their stores measured them."""
    return Subquery(
        models.DataArray.objects.filter(dataset=OuterRef("pk"))
        .order_by()
        .values("dataset")
        .annotate(total=Sum("store__size_bytes"))
        .values("total")[:1],
        output_field=BigIntegerField(),
    )


def sparse_dataset_size_bytes() -> Expression:
    """The bytes of a sparse matrix: both layouts share one store, so the matrix's bytes are that store's."""
    return Subquery(
        models.SparseArray.objects.filter(dataset=OuterRef("pk")).values(
            "store__size_bytes"
        )[:1],
        output_field=BigIntegerField(),
    )
