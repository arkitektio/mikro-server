"""The whole lens of a container, from the historical models: shared by the migrations that repoint layers onto lenses.

Plain Python beside the migrations, not service code: it builds a kind's child row and its
base row the way `core.logic.coordinate_system.whole_lens` does, from nothing but the
database, so that a layer written before every layer drew through a lens can be given the
lens it meant.
"""

from __future__ import annotations

#: (lens model, container FK field) per container model name.
LENS_KINDS: dict[str, tuple[str, str]] = {
    "ArrayDataset": ("ArrayLens", "dataset"),
    "TableDataset": ("TableLens", "table_dataset"),
    "SparseDataset": ("SparseLens", "sparse_dataset"),
    "MeshCollection": ("MeshLens", "mesh_collection"),
    "NetworkCollection": ("NetworkLens", "network_collection"),
    "AnnotationCollection": ("AnnotationLens", "annotation_collection"),
}


def whole_lens_id(apps, container_model: str, container_id: int) -> int:  # noqa: ANN001 - the migration's app registry
    """The pk of the container's whole lens, made when it is not there."""
    kind_name, field = LENS_KINDS[container_model]
    Kind = apps.get_model("core", kind_name)
    selection = {"slices": []} if kind_name == "ArrayLens" else {"windows": []}
    existing = Kind.objects.filter(**{f"{field}_id": container_id}, **selection).order_by("pk").values_list("pk", flat=True).first()
    if existing is not None:
        return existing

    Container = apps.get_model("core", container_model)
    ContentType = apps.get_model("contenttypes", "ContentType")
    container = Container.objects.get(pk=container_id)
    ctype, _ = ContentType.objects.get_or_create(app_label="core", model=kind_name.lower())
    lens = Kind(**{f"{field}_id": container_id}, coordinate_system_id=container.coordinate_system_id, organization_id=container.organization_id, polymorphic_ctype_id=ctype.pk, **selection)
    lens.save()
    return lens.pk
