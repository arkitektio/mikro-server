"""Spatial queries over annotations that no filter can express.

Filtering is a predicate; nearest-k is an *ordering* plus a cut, so it lives here
as a root query rather than on AnnotationFilter. The cube ``<->`` distance
operator is GiST-accelerated when it appears in ORDER BY against the indexed
column, which is exactly the shape this resolver emits.
"""

import strawberry
from django.db.models import FloatField
from django.db.models.expressions import RawSQL
from kante.types import Info

from core import models, types
from core.fields import cube_literal
from core.scoping import get_for_org


def nearest_annotations(
    info: Info,
    collection: strawberry.ID,
    point: list[float],
    limit: int = 10,
) -> list[types.Annotation]:
    """The k annotations of one collection nearest to a point.

    Distance is the cube distance between the probe point and each annotation's
    intrinsic bounding box (0 inside the box). Scoped to one collection because
    boxes only compare within one frame -- the point is given in the collection's
    nearest-intrinsic space, in its coordinate order. Annotations without a box
    (no vectors) are excluded: they are nowhere, not near.
    """
    resolved = get_for_org(models.AnnotationCollection, info, id=collection)
    probe = cube_literal(point)
    return list(
        models.Annotation.objects.filter(collection=resolved, bbox_cube__isnull=False)
        .select_related("collection__coordinate_system")
        .annotate(distance=RawSQL("bbox_cube <-> %s::cube", (probe,), output_field=FloatField()))
        .order_by("distance")[: max(1, limit)]
    )
