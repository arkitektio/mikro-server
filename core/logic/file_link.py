"""Writing the links between a file's bytes and the data held in a container.

The one writer for both directions. An ingest (``sourceFiles`` on a container's create
mutation) and an export (``exportOf`` on ``fromFileLike``) are the same sentence said from
opposite ends -- *these bytes and this data are the same thing, and one was made from the
other* -- so they write the same row and differ only in ``direction``.

Deliberately not part of :mod:`core.logic.coordinate_system`. That module writes edges of the
coordinate graph, and every edge relates two spaces; a file has no space. Keeping the two apart
is the whole point of the split, and a reader who finds file lineage here rather than in
``write_derivation_edges`` has found the answer to why.

The contract is ``write_derivation_edges``', kept deliberately: resolve everything before
writing anything, so a mistyped third entry does not leave the first two behind as a
half-recorded lineage.
"""

from collections.abc import Sequence

from django.db import transaction

from core import enums, models
from core.creation import CreationContext
from core.scoping import get_for_org


#: The ``FileLink`` FK each container model is stored under. The one place the container's
#: type meets the column, so the ingest side (which is handed a model instance) and the export
#: side (which is handed a discriminator) cannot disagree about where a link is written.
_CONTAINER_FIELDS: dict[type, str] = {
    models.ArrayDataset: "dataset",
    models.TableDataset: "table_dataset",
    models.MeshCollection: "mesh_collection",
    models.AnnotationCollection: "annotation_collection",
}

#: The model each ``FileLinkContainerKind`` names, for the export direction.
_CONTAINER_MODELS: dict[str, type] = {
    enums.FileLinkContainerKind.DATASET.value: models.ArrayDataset,
    enums.FileLinkContainerKind.TABLE_DATASET.value: models.TableDataset,
    enums.FileLinkContainerKind.MESH_COLLECTION.value: models.MeshCollection,
    enums.FileLinkContainerKind.ANNOTATION_COLLECTION.value: models.AnnotationCollection,
}


def container_field(container) -> str:  # noqa: ANN001 - one of the four container models
    """The ``FileLink`` column this container is stored under.

    Raises rather than defaulting: a fifth container reaching this function is a wiring
    mistake, and writing its links into a column that silently does not exist would lose them.
    """
    field = _CONTAINER_FIELDS.get(type(container))
    if field is None:
        raise ValueError(f"A {type(container).__name__} cannot be linked to a file: only an array dataset, a table dataset, a mesh collection or an annotation collection holds data a file encodes.")
    return field


def column_for_kind(kind: "enums.FileLinkContainerKind | str") -> str:
    """The ``FileLink`` column a container *kind* is stored under.

    The read-side counterpart of :func:`container_field`, which answers the same question from
    a fetched model instance. Composed from the two tables above rather than being a third
    one, so the filters cannot drift from the writers about which column a kind means.
    """
    value = kind.value if hasattr(kind, "value") else kind
    model = _CONTAINER_MODELS.get(value)
    if model is None:
        raise ValueError(f"'{value}' is not a container a file link can point at. Expected one of: {', '.join(sorted(_CONTAINER_MODELS))}.")
    return _CONTAINER_FIELDS[model]


def container_label(container) -> str:  # noqa: ANN001 - one of the four container models
    """A container as it should read in a message.

    Not ``container.name``: a ``MeshCollection`` carries ``version`` instead, so reading the
    name directly turns a refusal that was about to explain itself into an ``AttributeError``.
    """
    return getattr(container, "name", None) or getattr(container, "version", None) or f"{type(container).__name__} {container.pk}"


def _refuse_duplicates(pairs: Sequence[tuple], *, subject: str) -> None:
    """Refuse a list naming the same (thing, series) twice, before anything is written.

    Keyed on the series as well as the id, because two series of one file are two genuinely
    different sources -- that is exactly why the series is part of the link's identity rather
    than a label on it. The database says the same thing with four partial unique constraints;
    this says it in a sentence first, so a client never sees an IntegrityError.
    """
    duplicates = sorted({_describe_pair(pair) for pair in pairs if pairs.count(pair) > 1})
    if duplicates:
        raise ValueError(
            f"Each entry must name a distinct {subject}, but {', '.join(duplicates)} appear{'s' if len(duplicates) == 1 else ''} more than once. "
            "One entry per source; name a `seriesIdentifier` if these are different parts of the same file."
        )


def _describe_pair(pair: tuple) -> str:
    """One duplicate, as it should read in the refusal."""
    identifier, series = pair[-2], pair[-1]
    return f"{identifier} (series '{series}')" if series else str(identifier)


def _existing_link(*, file_id: int, container_field_name: str, container_id: int, direction: str, series: str) -> bool:
    """Whether this exact link is already recorded."""
    return models.FileLink.objects.filter(
        **{
            "file_id": file_id,
            f"{container_field_name}_id": container_id,
            "direction": direction,
            "series_identifier": series,
        }
    ).exists()


def write_file_links(info, *, container, source_files: Sequence, ctx: CreationContext) -> list["models.FileLink"]:  # noqa: ANN001 - kante's Info, and a container model
    """Record the files a container was produced from. The ingest direction.

    Every id is org-scoped through ``get_for_org`` like any other id a client sends, and every
    file is fetched before any row is written.
    """
    if not source_files:
        return []

    field_name = container_field(container)
    _refuse_duplicates([(entry.file, entry.series_identifier or "") for entry in source_files], subject="file")

    files = [get_for_org(models.File, info, id=entry.file) for entry in source_files]

    # Checked here, in the resolve phase, and not in the write loop below: an entry that
    # collides with a link already on record must not leave the entries before it written.
    for entry, file in zip(source_files, files):
        series = entry.series_identifier or ""
        if _existing_link(file_id=file.pk, container_field_name=field_name, container_id=container.pk, direction=enums.FileLinkDirectionChoices.SOURCE.value, series=series):
            raise ValueError(f"'{container_label(container)}' already records file '{file.name}'{f' (series {series!r})' if series else ''} as a source. One link per file and series.")

    links: list[models.FileLink] = []
    with transaction.atomic():
        for entry, file in zip(source_files, files):
            series = entry.series_identifier or ""
            links.append(
                models.FileLink.objects.create(
                    file=file,
                    direction=enums.FileLinkDirectionChoices.SOURCE.value,
                    series_identifier=series,
                    value_relation=entry.value_relation.value if entry.value_relation else None,
                    creator=ctx.user,
                    organization=ctx.organization,
                    **{field_name: container},
                    **ctx.provenance_kwargs(),
                )
            )
    return links


def write_export_links(info, *, file: "models.File", export_of: Sequence, ctx: CreationContext) -> list["models.FileLink"]:  # noqa: ANN001 - kante's Info
    """Record the containers a file was written from. The export direction.

    The mirror of :func:`write_file_links`, and the reason both live here: one file exported
    from a dataset and one dataset ingested from a file are the same relation, stated from
    opposite ends, and a second module for the second direction would let them drift.
    """
    if not export_of:
        return []

    lowered = [(entry.kind.value if hasattr(entry.kind, "value") else entry.kind, entry.container_id, entry.series_identifier or "", entry.value_relation) for entry in export_of]
    _refuse_duplicates([(kind, container_id, series) for kind, container_id, series, _ in lowered], subject="container")

    containers = [get_for_org(_CONTAINER_MODELS[kind], info, id=container_id) for kind, container_id, _, _ in lowered]
    fields = [container_field(container) for container in containers]

    # Resolve phase, like the ingest direction: a collision on the third entry must not leave
    # the first two written.
    for (_, _, series, _), container, field_name in zip(lowered, containers, fields):
        if _existing_link(file_id=file.pk, container_field_name=field_name, container_id=container.pk, direction=enums.FileLinkDirectionChoices.RENDITION.value, series=series):
            raise ValueError(f"'{file.name}' already records '{container_label(container)}' as what it was written from{f' (series {series!r})' if series else ''}. One link per container and series.")

    links: list[models.FileLink] = []
    with transaction.atomic():
        for (_, _, series, value_relation), container, field_name in zip(lowered, containers, fields):
            links.append(
                models.FileLink.objects.create(
                    file=file,
                    direction=enums.FileLinkDirectionChoices.RENDITION.value,
                    series_identifier=series,
                    value_relation=value_relation.value if value_relation else None,
                    creator=ctx.user,
                    organization=ctx.organization,
                    **{field_name: container},
                    **ctx.provenance_kwargs(),
                )
            )
    return links


def links_for(container, direction: "enums.FileLinkDirectionChoices"):  # noqa: ANN001, ANN201 - one of the four container models; returns a QuerySet
    """A container's links in one direction, oldest first.

    Shared by the ``sourceFiles`` and ``exports`` fields on all four container types, so the
    two cannot disagree about ordering or about which column they read.

    Returns a **queryset**, not a list, so the resolvers can hand it to
    ``strawberry_django.filters.apply`` before evaluating it. Returning a list here is what
    left ``FileLinkFilter`` declared but unreachable, and therefore absent from the SDL.
    """
    return container.file_links.filter(direction=direction.value).select_related("file").order_by("pk")


#: The media types a colour photograph arrives in. Consumer picture formats, every one of
#: them: a converter that read a PNG or a JPEG to write these arrays read a picture, and its
#: three channels are the red, green and blue of one image rather than three signals.
#:
#: TIFF is deliberately absent though it can hold RGB, and it is the entry worth explaining:
#: on a microscopy server a TIFF is overwhelmingly an acquisition -- OME-TIFF, a stack, a
#: multi-channel export -- so it is the one format where the inference would be wrong more
#: often than right. The proprietary formats (CZI, LIF, ND2) are absent for the same reason,
#: stated the easy way round.
_PHOTOGRAPHIC_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp", "image/gif"})

#: The same answer from the file's name, for an uploader that recorded no media type. Suffixes
#: rather than a parse: a name is a string a person typed, and ".JPEG" is the same picture.
_PHOTOGRAPHIC_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def is_photographic_source(container) -> bool:  # noqa: ANN001 - one of the four container models
    """Whether this container's data was read out of a consumer picture format.

    Evidence for the scene bootstrap, which needs to tell a colour photograph from a
    three-marker fluorescence acquisition and cannot do it from the shape (see
    :func:`core.logic.scene._infer_kind`). A source link says a converter read *these bytes*
    to write this data, so a PNG on the source side is a statement that the arrays came from
    a picture -- recorded at ingest, by the converter, rather than guessed here.

    SOURCE only. A RENDITION link is the opposite sentence: a PNG *written from* a
    fluorescence dataset says only that someone exported a preview, and a snapshot of a
    two-channel acquisition must not turn that acquisition into a photograph.
    """
    for link in links_for(container, enums.FileLinkDirectionChoices.SOURCE):
        file = link.file
        if file is None:
            continue
        if (file.content_type or "").split(";")[0].strip().lower() in _PHOTOGRAPHIC_MEDIA_TYPES:
            return True
        if (file.name or "").lower().endswith(_PHOTOGRAPHIC_SUFFIXES):
            return True
    return False
