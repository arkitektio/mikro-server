"""Which S3-backed stores a deletion leaves behind, and whether anything still wants them.

Every ``store`` foreign key lives on the *data* model and points at the store with
``on_delete=CASCADE``, so cascade runs store -> data and never the reverse: deleting a ``File``
or an ``ArrayDataset`` orphans both the store row and its bytes, permanently. Nothing here changes
that direction -- a store outliving its data by a moment is correct, since the bytes must not
vanish inside a transaction that might roll back. What this module does is *notice*, so
``purge_orphaned_stores`` can collect them later.

Two rules the rest of the system depends on:

- **A flag is a candidate, never an authority.** ``orphaned_at`` records that nothing referenced
  the store at the moment its last referrer went. It is re-checked at purge time, because a
  store can be shared (no ``store`` FK has a unique constraint) or re-attached afterwards.
- **Only flagged stores are ever collectable.** An *unreferenced* store is not garbage: an
  upload grant creates the row before the client has uploaded and long before any data row
  attaches, so unreferenced is the normal state of a live upload.

**Collecting and flagging are two calls, and must stay two.** :func:`stores_orphaned_by` runs
*before* the delete, because the cascade has to still be walkable; :func:`flag_orphaned` runs
*after* it, so a delete that raises flags nothing. `core.mutations._generic` straddles
``item.delete()`` with them deliberately. A ``flag_stores_orphaned_by`` convenience wrapper that
did both in one call used to live here, documented as "what the delete mutations use" -- it never
was, nothing ever called it, and following its instruction to call it before the delete would
flag stores that a rollback was about to save. It was removed rather than fixed: there is no
correct one-call shape, because the delete belongs *between* the two halves.
"""

from collections.abc import Iterable

from django.db.models.deletion import Collector
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.utils import timezone

from datalayer.models import DatalayerStore


def _store_fields(model: type) -> list[str]:
    """The names of ``model``'s fields that reference a store, FK and M2M alike."""
    names = []
    for field in model._meta.get_fields():
        if not isinstance(field, (ForeignKey, ManyToManyField)):
            continue
        related = getattr(field, "related_model", None)
        if related is not None and issubclass(related, DatalayerStore):
            names.append(field.name)
    return names


def referrers_of(store: DatalayerStore) -> list[tuple[str, int]]:
    """Every relation still pointing at this store, as ``(accessor, count)`` pairs.

    Derived from ``_meta.related_objects`` on the store's *real* (downcast) class rather than
    from a hand-kept registry, so a new model with a ``store`` FK is covered the day it is
    written -- which is exactly the drift a registry would suffer. It picks up many-to-many
    referrers too (``MeshCollection.geometry`` is one), where ``on_delete`` is ``None``.

    Only non-empty relations are returned, so a truthy result means "something still wants
    these bytes".
    """
    # The base class's related_objects would miss the subclass-specific referrers: `File.store`
    # points at `BigFileStore`, not at `DatalayerStore`.
    real = store.get_real_instance() if hasattr(store, "get_real_instance") else store

    found = []
    for relation in real._meta.related_objects:
        accessor = relation.get_accessor_name()
        if accessor is None:
            continue
        manager = getattr(real, accessor, None)
        if manager is None:
            continue
        count = manager.count()
        if count:
            found.append((accessor, count))
    return found


def _doomed_rows(collector: Collector) -> list[tuple[type, Iterable]]:
    """Everything a collected cascade will delete, from *both* of the collector's paths.

    `collector.data` is only half the answer. Django takes a **fast-delete** shortcut for any
    model with no cascading children and no delete signals: those rows are never instantiated,
    they go into `collector.fast_deletes` as querysets, and they are invisible in `.data`.

    `DataArray` is exactly such a model, which made this the bug that mattered: deleting an
    `ArrayDataset` flagged nothing at all, because every pyramid level took the shortcut. The
    querysets are evaluated here -- a few rows per delete, and the alternative is losing them.
    """
    rows: list[tuple[type, Iterable]] = [(model, doomed) for model, doomed in collector.data.items()]
    rows.extend((queryset.model, list(queryset)) for queryset in collector.fast_deletes)
    return rows


def stores_orphaned_by(instance) -> list[DatalayerStore]:  # noqa: ANN001 - any model with, or cascading to, a store
    """Every store the deletion of ``instance`` would leave unreferenced.

    Walks the *real* cascade with Django's ``Collector``, so an ``ArrayDataset`` yields the
    ``ZarrStore`` of every pyramid level: those hang off ``DataArray`` rows, not off the
    dataset, and a naive one-hop walk of the instance's own fields would miss all of them.

    ``Collector.collect`` traverses reverse relations only and never forward FKs, so none of
    this app's ``PROTECT`` keys (all forward, into ``CoordinateSystem``) is on the path. The one
    ``PROTECT`` aimed at a container -- ``Column.references`` -> ``TableDataset`` -- already
    makes that delete raise, so collecting first surfaces the identical ``ProtectedError`` a
    moment earlier. Deliberately not caught: the client message is unchanged.
    """
    collector = Collector(using=instance._state.db)
    collector.collect([instance])

    stores: dict[int, DatalayerStore] = {}
    for model, doomed in _doomed_rows(collector):
        fields = _store_fields(model)
        if not fields:
            continue
        for obj in doomed:
            for name in fields:
                value = getattr(obj, name, None)
                if value is None:
                    continue
                # An M2M gives a manager; a FK gives the store itself. The collector deletes
                # through-rows but never their targets, so the M2M side has to be walked here
                # or a mesh collection's geometry shards are silently kept forever.
                if hasattr(value, "all"):
                    for store in value.all():
                        stores[store.pk] = store
                else:
                    stores[value.pk] = value

    return list(stores.values())


def flag_orphaned(stores: Iterable[DatalayerStore]) -> int:
    """Mark these stores as collectable, and return how many were marked.

    Stamped now rather than at purge time so the grace period runs from the deletion the user
    actually performed. A store flagged, un-flagged by a re-check, then orphaned again is
    stamped afresh -- the clock restarts from the second deletion, which is the honest reading
    of "deleted seven days ago".
    """
    pks = [store.pk for store in stores]
    if not pks:
        return 0
    return DatalayerStore.objects.filter(pk__in=pks).update(orphaned_at=timezone.now())
