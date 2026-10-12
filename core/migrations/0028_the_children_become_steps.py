"""The child rows of a composite edge become its steps, and go.

A SEQUENCE's children, in their order, become its `steps`. A BY_DIMENSION's children were a
projection of its own `params` (`_project_by_dimension_children`), or the one IDENTITY child
of an identity registration whose empty `params` already mean the identity; where a row
carried no map of its own and its children did, their parameters are folded in, in the order
they applied. Then every child row -- and its history, which was never an edge's -- goes.
The data step alone, between 0027 (the column it writes) and 0029 (the columns it reads).
"""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

_MAP_KEYS = ("scale", "translation", "affine")


def _step(child) -> dict:  # noqa: ANN001 - a historical model instance
    step = {"kind": child.kind, "params": child.params or {}}
    if child.field_id is not None:
        step["field"] = child.field_id
    return step


def children_become_steps(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Fold every wrapper's children into the wrapper, then delete the children."""
    Transformation = apps.get_model("core", "Transformation")
    HistoricalTransformation = apps.get_model("core", "HistoricalTransformation")

    by_parent: dict[int, list] = {}
    for child in Transformation.objects.filter(parent__isnull=False).order_by("parent_id", "order", "pk"):
        by_parent.setdefault(child.parent_id, []).append(child)

    for parent in Transformation.objects.filter(pk__in=by_parent):
        children = by_parent[parent.pk]
        if parent.kind == "SEQUENCE":
            parent.steps = [_step(child) for child in children]
            parent.save(update_fields=["steps"])
            continue
        params = dict(parent.params or {})
        if any(key in params for key in _MAP_KEYS):
            continue  # the children were a projection of these params; nothing to copy
        for child in children:
            params.update(child.params or {})
        if params != (parent.params or {}):
            parent.params = params
            parent.save(update_fields=["params"])

    Transformation.objects.filter(parent__isnull=False).delete()
    HistoricalTransformation.objects.filter(parent__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0027_a_sequence_carries_its_steps")]

    operations = [migrations.RunPython(children_become_steps, migrations.RunPython.noop)]
