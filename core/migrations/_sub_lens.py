"""A channel-pinned lens from the historical models: shared by the migration that gives indexed layers the lens they meant.

Plain Python beside the migrations, not service code, like ``_whole_lens``: it builds the lens'
coordinate system, its axes, the array-lens row and the edge back into the dataset's grid the
way ``core.logic.coordinate_system.create_lens`` does, from nothing but the database. The one
import from the service is ``core.logic.coords``: pure arithmetic over lists -- how slices
compose, normalize and map onto the parent grid -- with no model, setting or query in it, which
is exactly the part a copy here would get subtly wrong.
"""

from __future__ import annotations

import dataclasses

from core.logic import coords


@dataclasses.dataclass(frozen=True)
class _Slice:
    axis: str
    start: int | None = None
    stop: int | None = None
    step: int | None = None


def slices_of(entries: list[dict]) -> list[_Slice]:
    return [_Slice(axis=entry["axis"], start=entry.get("start"), stop=entry.get("stop"), step=entry.get("step")) for entry in entries or []]


def dataset_geometry(apps, dataset_id: int) -> tuple[list[int], list[str], list[str]]:  # noqa: ANN001 - the migration's app registry
    """The dataset's level-0 shape, and its axis names and types in array order."""
    ArrayDataset = apps.get_model("core", "ArrayDataset")
    DataArray = apps.get_model("core", "DataArray")
    Axis = apps.get_model("core", "Axis")
    dataset = ArrayDataset.objects.get(pk=dataset_id)
    level = DataArray.objects.filter(dataset_id=dataset_id).order_by("level").values_list("shape", flat=True).first()
    axes = list(Axis.objects.filter(coordinate_system_id=dataset.coordinate_system_id).order_by("order"))
    return (list(level) if isinstance(level, list) else []), [axis.name for axis in axes], [axis.type for axis in axes]


def sub_lens_id(apps, parent_lens_id: int, axis: str, local_index: int) -> int:  # noqa: ANN001 - the migration's app registry
    """The pk of the dataset's lens that keeps one position of ``axis`` of the parent lens: the row that exists, or a new one.

    ``local_index`` counts along the parent lens, as the layer's index did; the result is the
    dataset's lens for the composed selection, so two layers indexing the same channel of the
    same lens share one row.
    """
    ArrayLens = apps.get_model("core", "ArrayLens")
    ArrayDataset = apps.get_model("core", "ArrayDataset")
    CoordinateSystem = apps.get_model("core", "CoordinateSystem")
    Axis = apps.get_model("core", "Axis")
    Transformation = apps.get_model("core", "Transformation")
    ContentType = apps.get_model("contenttypes", "ContentType")

    parent = ArrayLens.objects.get(pk=parent_lens_id)
    dataset = ArrayDataset.objects.get(pk=parent.dataset_id)
    shape, names, _ = dataset_geometry(apps, dataset.pk)
    composed = coords.compose_slices(shape, names, slices_of(parent.slices), [_Slice(axis=axis, start=local_index, stop=local_index + 1)])
    normalized = coords.normalize_slices(shape, names, slices_of(composed))
    existing = ArrayLens.objects.filter(dataset_id=dataset.pk, slices=normalized).order_by("pk").values_list("pk", flat=True).first()
    if existing is not None:
        return existing

    system = CoordinateSystem.objects.create(name=f"{dataset.name}/lens", organization_id=dataset.organization_id)
    Axis.objects.bulk_create(
        [
            Axis(coordinate_system=system, order=source.order, name=source.name, type=source.type, unit=None, long_name=source.long_name, description=source.description)
            for source in Axis.objects.filter(coordinate_system_id=dataset.coordinate_system_id).order_by("order")
        ]
    )
    ctype, _ = ContentType.objects.get_or_create(app_label="core", model="arraylens")
    lens = ArrayLens(dataset_id=dataset.pk, coordinate_system_id=system.pk, organization_id=dataset.organization_id, polymorphic_ctype_id=ctype.pk, slices=normalized)
    lens.save()

    kind, params = coords.lens_to_parent(names, slices_of(normalized))
    if kind == "TRANSLATION":
        Transformation.objects.create(kind=kind, input_id=system.pk, output_id=dataset.coordinate_system_id, params=params, organization_id=dataset.organization_id)
    else:
        sequence = Transformation.objects.create(kind="SEQUENCE", input_id=system.pk, output_id=dataset.coordinate_system_id, params={}, validity="VALIDATED", organization_id=dataset.organization_id)
        Transformation.objects.create(kind="SCALE", parent=sequence, order=0, params={"scale": params["scale"]}, organization_id=dataset.organization_id)
        Transformation.objects.create(kind="TRANSLATION", parent=sequence, order=1, params={"translation": params["translation"]}, organization_id=dataset.organization_id)
    return lens.pk
