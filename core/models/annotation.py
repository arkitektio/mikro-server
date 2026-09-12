"""Human-drawn annotations and the collection that owns their drawing space.

An :class:`Annotation` is the CRUD counterpart of the parquet-backed machine
ROIs a :class:`~core.models.TableDataset` carries: an addressable, mutable,
owned shape a person draws and edits, not a million rows a pipeline emits.
Annotations never stand alone -- each belongs to an
:class:`AnnotationCollection`, and the collection owns its own INTRINSIC
coordinate system exactly like a mesh collection owns its vertex space. How the
drawing space relates to anything else -- a scene's world, a dataset's pixel
grid -- is a :class:`~core.models.Transformation` edge, never a second FK on
the shape.
"""

import uuid

from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.postgres.indexes import GinIndex, GistIndex

from authentikate.models import Organization
from koherent.fields import ProvenanceField
from django_choices_field import TextChoicesField

from core import enums
from core.fields import CubeField


class AnnotationCollection(models.Model):
    """A named set of human-drawn annotations, owning the space they are drawn in.

    The collection is the coordinate-system owner (the FK lives on
    ``CoordinateSystem.annotation_collection``, like every other container), so
    all its annotations share one drawing space and one registration story: the
    collection registers into a world, the shapes just have vectors.

    ``scene`` marks a collection minted as that scene's default drawing surface
    by ``createAnnotation(scene:)`` -- one per scene, enforced by the database.
    It is a bookkeeping FK, not placement: placement is the identity
    registration edge authored into the scene's world when the collection is
    minted. SET_NULL keeps the invariant that deleting a scene never deletes
    what was drawn: only the layer cascades away; the world, the registration
    edge into it, the collection and its annotations all survive -- the
    collection stays placed in the surviving space. Two scenes composing over
    one shared space each mint their *own* collection; scene B sees scene A's
    annotations only through reachability, and renders them only once it gets
    its own layer for A's collection.
    """

    name = models.CharField(max_length=255, help_text="The name of this annotation collection")
    description = models.TextField(null=True, blank=True, help_text="A free-form description of this annotation collection")

    # Filing, not placement -- see the note on `ArrayDataset.folder`. Distinct from `scene`
    # below, which is also bookkeeping but answers a different question: `scene` says which
    # drawing surface minted this collection, `folder` says where a user keeps it.
    folder = models.ForeignKey(
        "Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotation_collections",
        help_text="The folder this annotation collection is filed in. Organisational only -- it says nothing about the space the shapes are drawn in",
    )
    coordinate_system = models.ForeignKey(
        "CoordinateSystem",
        on_delete=models.PROTECT,
        # Nullable in the database only because the `historical*` twin carries rows written
        # before this column existed, and a history row must be allowed to say "not
        # recorded". Every write path sets it, so a live row never has none.
        null=True,
        blank=True,
        related_name="annotation_collections",
        help_text="The coordinate system this collection's shapes are drawn in. All its annotations share it, so they share one placement story: the collection is related to other spaces by edges, the shapes just have vectors",
    )
    scene = models.OneToOneField(
        "Scene",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotation_collection",
        help_text="The scene this collection was minted for as its default drawing surface, if any. One per scene; bookkeeping only, placement is the registration edge",
    )

    # The frame every annotation's `intrinsic_bbox` is written in, named rather than
    # re-derived. `nearestAnnotations` and `AnnotationFilter.intersects` both say boxes
    # only compare within one frame, and until this existed nothing in the schema *named*
    # that frame -- so two collections' boxes could be compared with nothing to stop it,
    # and a spatial query could only recover the frame by re-walking `path_to_intrinsic`
    # (a query per hop, and a second copy of a walk that can silently disagree with the
    # first). Written once when the collection is created and never after: the boxes are
    # stored against it, so a later change would relabel numbers nobody recomputed.
    # **Null means the collection's own system** -- the same convention, for the same reason,
    # as `Transformation.field`: PROTECT is right for a *separate* system (deleting the
    # dataset a collection's boxes are denominated in must not silently relabel them), but a
    # self-reference is a fact about this collection, which its own CASCADE already removes
    # with it. Written as a real self-FK, PROTECT would win that race and the collection could
    # never be deleted at all. Read it through `effective_bbox_system`, never directly.
    bbox_system = models.ForeignKey(
        "CoordinateSystem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="annotation_bbox_frames",
        help_text=(
            "The coordinate system this collection's stored bounding boxes are expressed in, when that is a system other than its own: the nearest intrinsic space its own "
            "system could reach at creation. Null when the boxes are in the collection's own space, which is the case whenever no chain resolves. Boxes only compare within "
            "one frame, and this names it. Written once at creation and immutable, because the stored boxes are numbers against it"
        ),
    )

    @property
    def effective_bbox_system(self):
        """The frame this collection's boxes are in: the stored one, or its own when null.

        The one reader of the null-means-own-system convention above, so the convention lives
        in exactly one place.
        """
        return self.bbox_system or self.coordinate_system_or_none

    created_at = models.DateTimeField(auto_now_add=True, help_text="The time this annotation collection was created")
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotation_collections",
        help_text="The user that created this annotation collection",
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, help_text="The organization this annotation collection belongs to")
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

    class Meta:
        """Meta options for the annotation collection."""

        ordering = ["-created_at"]

    def __str__(self) -> str:
        """The collection's name."""
        return self.name

    @property
    def coordinate_system_or_none(self):
        """The coordinate system this collection owns, or None before it is created."""
        # The reverse of CoordinateSystem.annotation_collection, which raises
        # rather than returning None when the system has not been created yet.
        return getattr(self, "coordinate_system", None)


class Annotation(models.Model):
    """A human-drawn shape: an addressable, mutable, owned entity in its collection's space.

    An annotation belongs to a **collection**, never to a scene. There is no
    ``scene_id`` column here and there must never be one: delete the scene and
    the annotation survives, because the space it is drawn in -- its
    collection's own system -- has not gone anywhere.

    ``intrinsic_bbox`` is the axis-aligned box of the shape in the nearest
    intrinsic space its collection's system can reach (the collection's own
    space when no chain resolves), denormalized for joins and culling. It is
    deliberately *not* a world box: world is scene-owned, and the same
    collection can sit in two scenes under two registrations, so a single
    stored world box would be wrong in one of them.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        AnnotationCollection,
        related_name="annotations",
        on_delete=models.CASCADE,
        help_text="The collection this annotation belongs to; its geometry is expressed in the collection's own coordinate system",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    kind = TextChoicesField(
        choices_enum=enums.AnnotationKindChoices,
        default=enums.AnnotationKindChoices.PATH.value,
        help_text="The geometric kind of the annotation (rectangle, polygon, path, ...), which fixes how its vectors are read",
    )
    # The same shape as CoordinateAnchor.coordinates, deliberately: one canonical
    # representation of "pinned to discrete coordinates", and a dict is
    # GIN-indexed (see Meta) so "every annotation on channel 0" is a containment
    # query. The GraphQL type still ships it as a typed [Coordinate] list.
    coordinates = models.JSONField(
        help_text="The discrete coordinates this annotation is pinned to, keyed by coordinate name, e.g. {'t': 0, 'c': 0}. A coordinate the annotation does not pin is one it spans",
        default=dict,
    )
    vectors = models.JSONField(help_text="A list of the annotation's vectors (specific for each kind), in the collection's coordinate system's own units", default=list)
    intrinsic_bbox = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "The annotation's axis-aligned bounding box in the frame its collection names (`bbox_system`, null meaning the collection's own space), as {'min': [...], 'max': [...]}. That frame is the nearest "
            "intrinsic space the collection's chain composes down to, which for a collection registered into a world, or derived across a rank change, is its own drawing space -- an edge that says "
            "nothing about an axis cannot be crossed to invent an extent along it. Derived from all corners of the geometry, never from min/max alone"
        ),
    )
    bbox_cube = CubeField(
        null=True,
        blank=True,
        editable=False,
        help_text="The intrinsic bounding box as a Postgres cube, denormalized from intrinsic_bbox at write time for GiST-indexed overlap/containment/nearest search. Write-only: the API reads intrinsic_bbox",
    )
    created_with_transforms = models.PositiveIntegerField(
        default=0,
        help_text="The version of the transformation chain this annotation was authored against. Provenance only: it is never used to resolve a coordinate",
    )

    # Per-shape styling lives here, not on the layer: the layer is one per
    # collection, and shapes within one collection may differ visually.
    stroke_color = models.JSONField(default=None, null=True, blank=True, help_text="The stroke (outline) color of the geometry (RGBA)")
    fill_color = models.JSONField(default=None, null=True, blank=True, help_text="The fill color of the geometry (RGBA), or null for no fill")
    stroke_width = models.FloatField(default=1.0, help_text="The stroke width of the geometry, in the drawing space's units. One number for every direction, so it is a well-defined length only where that space's axes share a scale (RFC-8)")
    filled = models.BooleanField(default=False, help_text="Whether the geometry is filled with fill_color")

    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotations",
        help_text="The user that drew this annotation",
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

    class Meta:
        """Meta options for the annotation: the two search indexes.

        Boxes only compare within one frame (a collection's nearest-intrinsic
        space), so every spatial query the GiST index serves is scoped to a
        collection or coordinate system by the filter layer.
        """

        indexes = [
            GinIndex(fields=["coordinates"], name="annotation_coords_gin"),
            GistIndex(fields=["bbox_cube"], name="annotation_bbox_gist"),
        ]

    def __str__(self) -> str:
        """The annotation's name and kind."""
        return f"{self.name} ({self.kind})"
