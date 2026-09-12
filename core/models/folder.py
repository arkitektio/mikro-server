from django.db import models
from django.contrib.auth import get_user_model
from django_choices_field import TextChoicesField
from koherent.fields import ProvenanceField
from authentikate.models import Organization, Membership
from taggit.managers import TaggableManager
from datalayer.models import BigFileStore, ParquetStore

from core import enums
from core.creation import CreationContext


class FolderManager(models.Manager):
    def get_current_default(self, ctx: CreationContext) -> "Folder":
        """Get (creating on first use) the user's default folder in the organization."""
        potential = self.filter(creator=ctx.user, organization=ctx.organization, membership=ctx.membership, is_default=True).first()
        if not potential:
            return self.create(
                creator=ctx.user,
                organization=ctx.organization,
                membership=ctx.membership,
                name="Default",
                is_default=True,
                **ctx.provenance_kwargs(),
            )

        return potential


class Folder(models.Model):
    """
    A folder is a collection of data files and metadata files.
    It mimics the concept of a folder in a file system and is the top level
    object in the data model.

    """

    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="created_folders",
        help_text="The user that created the folder",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="The time the folder was created")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    name = models.CharField(max_length=200, help_text="The name of the folder")
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="folders")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    description = models.CharField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="The description of the folder",
    )
    pinned_by = models.ManyToManyField(
        get_user_model(),
        related_name="pinned_folders",
        blank=True,
        help_text="The users that have pinned the folder",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Whether the folder is the current default folder for the user",
    )
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this object was created through, if any",
    )
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_%(class)ss",
        help_text="The assigner of the creating task, denormalized for fast filtering",
    )
    provenance = ProvenanceField()
    tags = TaggableManager()

    objects = FolderManager()

    def __str__(self) -> str:
        return super().__str__()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["creator", "is_default", "organization"],
                name="unique_default_per_creator",
                condition=models.Q(is_default=True),
            ),
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="only_one_folder_per_parent_and_name",
            ),
        ]


class File(models.Model):
    # Nullable, and SET_NULL: deleting a folder unfiles what is in it and destroys nothing.
    # It was CASCADE and NOT NULL, which made `releaseFilesFromFolder` an IntegrityError --
    # it sets this to None and the column would not take it.
    folder = models.ForeignKey(Folder, on_delete=models.SET_NULL, null=True, blank=True, related_name="files")
    store = models.ForeignKey(
        BigFileStore,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The store of the file",
    )
    name = models.CharField(max_length=1000, help_text="The name of the file", default="")
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True)
    organization = models.ForeignKey(
        "authentikate.Organization",
        on_delete=models.CASCADE,
        related_name="files",
    )
    size = models.BigIntegerField(help_text="The size of the file in bytes", null=True, blank=True)
    content_type = models.CharField(max_length=1000, help_text="The content type of the file", null=True, blank=True)
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="files")
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this object was created through, if any",
    )
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_%(class)ss",
        help_text="The assigner of the creating task, denormalized for fast filtering",
    )

    provenance = ProvenanceField()


class FileLink(models.Model):
    """A file and a container holding the same data, and which of the two was made from the other.

    **Deliberately not a Transformation.** A derivation is an edge of the coordinate graph --
    every member of ``DerivedFromInput`` resolves to a ``CoordinateSystem``, because the thing
    a derivation states is how one space maps into another. A file has no space. Every edge it
    could carry would be UNMAPPABLE by construction, which is a node and an edge in a geometry
    graph carrying no geometry, and a coordinate system for a PDF.

    The model already has the right concept for a file, and it is not a container: a
    ``DataArray`` points at its ``ZarrStore`` with a plain FK, and nobody has ever suggested a
    Zarr store needs a space. A file is that same thing seen at ingest or export time --
    *bytes*, not data in a space -- so this relates bytes to data and ``derivedFrom`` is left
    to relate spaces to spaces.

    The near-miss worth recording, since it argues the other way: a table with no COORDINATE
    columns is just as non-spatial and ``create_table_dataset`` mints it a system regardless,
    with a synthetic ``object`` axis of type INDEX. The difference is that that axis genuinely
    enumerates the table's rows, and a file's bytes are indexed by nothing the graph asks about.
    """

    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="links", help_text="The file side of the link")

    # Exactly one is set, the same fan-of-nullable-FKs shape `Layer` uses for its sources, and
    # enforced the same way `Layer` enforces it: in the mutations, not by a CheckConstraint.
    # ("Exactly one of four" needs a Case/When sum, and there is no CheckConstraint anywhere
    # in this app to follow.)
    #
    # **Be precise about where that guard actually lives**, because it is one place and not
    # the obvious one: `core.mutations.file_link.link_file`, which refuses an input naming
    # more than one container. The writers below it never see the question --
    # `write_file_links` takes a single already-fetched container object, and
    # `write_export_links` gets one per union member -- so neither can enforce it. A future
    # call site that builds a `FileLink` directly can therefore set two FKs and nothing will
    # object. That is the cost of having no constraint; if it ever bites, the fix is a
    # `CheckConstraint` here rather than a fourth copy of the check.
    dataset = models.ForeignKey("ArrayDataset", on_delete=models.CASCADE, null=True, blank=True, related_name="file_links", help_text="(DATASET) The array dataset side of the link")
    table_dataset = models.ForeignKey("TableDataset", on_delete=models.CASCADE, null=True, blank=True, related_name="file_links", help_text="(TABLE_DATASET) The table dataset side of the link")
    mesh_collection = models.ForeignKey("MeshCollection", on_delete=models.CASCADE, null=True, blank=True, related_name="file_links", help_text="(MESH_COLLECTION) The mesh collection side of the link")
    annotation_collection = models.ForeignKey("AnnotationCollection", on_delete=models.CASCADE, null=True, blank=True, related_name="file_links", help_text="(ANNOTATION_COLLECTION) The annotation collection side of the link")

    direction = TextChoicesField(
        choices_enum=enums.FileLinkDirectionChoices,
        help_text="Which side was made from the other: SOURCE for an ingest (the file existed first), RENDITION for an export (the container did). Stored because nothing else records it",
    )

    # The empty string, **not** null. Postgres treats NULLs as distinct in a unique index, so a
    # nullable column here would let two identical unqualified links coexist while the
    # constraints below claimed otherwise.
    series_identifier = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Which part of the file this link concerns -- the series of a multi-series LIF or CZI. Empty when the file holds one thing. Part of the link's identity, so one dataset fused from two series of one file is two links",
    )
    value_relation = TextChoicesField(
        choices_enum=enums.ValueRelationChoices,
        null=True,
        blank=True,
        help_text="What the conversion did to the values: IDENTICAL for a lossless transcode, TRANSFORMED for a projection written to PNG. Null means unstated",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name="file_links")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="file_links")
    created_through = models.ForeignKey(
        "koherent.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_%(class)ss",
        help_text="The task this object was created through, if any",
    )
    # Explicit, and load-bearing: `self_owner` reads `created_through_by_id` to decide who may
    # delete a link, and `ProvenanceField` does not supply that column.
    created_through_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_%(class)ss",
        help_text="The assigner of the creating task, denormalized for fast filtering",
    )

    provenance = ProvenanceField()

    class Meta:
        """One partial unique constraint per container."""

        # One partial constraint per container, because the key spans a different column each
        # time and a NULL container FK would otherwise make every row distinct. The writer
        # refuses duplicates first, so a client sees a sentence; these are the backstop.
        constraints = [
            models.UniqueConstraint(
                fields=["file", "dataset", "direction", "series_identifier"],
                condition=models.Q(dataset__isnull=False),
                name="unique_file_link_per_dataset",
            ),
            models.UniqueConstraint(
                fields=["file", "table_dataset", "direction", "series_identifier"],
                condition=models.Q(table_dataset__isnull=False),
                name="unique_file_link_per_table_dataset",
            ),
            models.UniqueConstraint(
                fields=["file", "mesh_collection", "direction", "series_identifier"],
                condition=models.Q(mesh_collection__isnull=False),
                name="unique_file_link_per_mesh_collection",
            ),
            models.UniqueConstraint(
                fields=["file", "annotation_collection", "direction", "series_identifier"],
                condition=models.Q(annotation_collection__isnull=False),
                name="unique_file_link_per_annotation_collection",
            ),
        ]
