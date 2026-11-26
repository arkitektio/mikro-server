"""Which files hold the same bytes, read off their stores' digests (arkitekt-datalayer).

A store's `digest` says what its bytes are and `sample` what they probably are (see
`arkitekt_datalayer.digest`); a store without a digest is one not hashed yet, and is a
duplicate of nothing until `digest_bigfiles` has run. Read only: nothing here merges or
shares stores -- two files with one digest both stand, and the user decides.

Every function takes an already scoped queryset of files (`for_org`), so the organization
and a guest's collaborations bound what a caller can learn: a digest is never matched across
organizations.
"""

from dataclasses import dataclass

from django.db.models import Count, F, Q, QuerySet

from core import models


def duplicates_of(
    file: models.File, scoped: QuerySet[models.File]
) -> QuerySet[models.File]:
    """The other files of ``scoped`` holding ``file``'s bytes: its own store, or one with the same digest and size."""
    store = file.store
    if store is None:
        return scoped.none()
    same = Q(store_id=store.pk)
    if store.digest is not None:
        same |= Q(store__digest=store.digest, store__size_bytes=store.size_bytes)
    return scoped.filter(same).exclude(pk=file.pk).order_by("created_at", "pk")


def candidates(
    scoped: QuerySet[models.File], size: int, sample: str
) -> QuerySet[models.File]:
    """The files of ``scoped`` whose bytes may be a file of ``size`` bytes with ``sample``, oldest first."""
    return scoped.filter(store__size_bytes=size, store__sample=sample).order_by(
        "created_at", "pk"
    )


def duplicated(scoped: QuerySet[models.File]) -> QuerySet[models.File]:
    """The files of ``scoped`` that share their digest and size with another file of ``scoped``."""
    keys = (
        scoped.filter(store__digest__isnull=False)
        .values("store__digest", "store__size_bytes")
        .annotate(n=Count("pk"))
        .filter(n__gt=1)
        .values("store__digest")
    )
    return scoped.filter(store__digest__in=keys)


@dataclass
class DuplicateGroup:
    """Files holding one set of bytes."""

    digest: str
    size: int
    files: list[models.File]

    @property
    def redundant_bytes(self) -> int:
        """What keeping one copy would free: every file past the first."""
        return self.size * (len(self.files) - 1)


def groups(
    scoped: QuerySet[models.File], offset: int = 0, limit: int | None = None
) -> list[DuplicateGroup]:
    """The sets of files in ``scoped`` sharing a digest and size, most redundant bytes first."""
    keys = (
        scoped.filter(store__digest__isnull=False, store__size_bytes__isnull=False)
        .values("store__digest", "store__size_bytes")
        .annotate(n=Count("pk"))
        .filter(n__gt=1)
        .annotate(redundant=F("store__size_bytes") * (F("n") - 1))
        .order_by("-redundant", "store__digest")
    )
    page = list(keys[offset : offset + limit] if limit is not None else keys[offset:])
    if not page:
        return []
    members = (
        scoped.filter(store__digest__in=[key["store__digest"] for key in page])
        .select_related("store")
        .order_by("created_at", "pk")
    )
    by_key: dict[tuple[str, int], list[models.File]] = {}
    for file in members:
        by_key.setdefault((file.store.digest, file.store.size_bytes), []).append(file)
    return [
        DuplicateGroup(
            digest=key["store__digest"],
            size=key["store__size_bytes"],
            files=by_key.get((key["store__digest"], key["store__size_bytes"]), []),
        )
        for key in page
    ]
