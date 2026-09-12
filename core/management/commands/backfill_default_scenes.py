"""Seed `ArrayDataset.default_scene` from the sole-occupancy rule `latestSnapshot` used to derive.

`latestSnapshot` used to answer by *deriving* a scene: the newest picture of a scene whose only
anchored dataset was this one. It now reads `ArrayDataset.default_scene`, a nomination. Without this
command every existing dataset would lose its thumbnail the moment that change deploys, so this
runs the old derivation once and writes its answer into the new column.

**This command is temporary, and so is the function below it.** `scenes_by_sole_dataset` lived in
`core.logic.graph` and was deleted from it: nothing on a request path walks it any more, and it
survives here only to seed the column. Delete this whole module -- and with it the last caller of
that walk -- once the backfill has run everywhere, no earlier than the release after the one
introducing `default_scene`.

The finish line is printed rather than left to memory: each run reports how many datasets still
have no default, which is the work remaining before this can go. The same number is queryable as
`array_datasets(filters: {hasDefaultScene: false})`.
"""

from collections.abc import Iterable

from django.core.management.base import BaseCommand
from django.db import transaction

from core import models
from core.logic.graph import is_traversable, residence_map


def scenes_by_sole_dataset(scenes: "Iterable[models.Scene]") -> dict[int, list["models.Scene"]]:
    """Each dataset id, mapped to the scenes whose *only* anchored dataset it is.

    Lifted verbatim from `core.logic.graph`, where it was the engine of `ArrayDataset.latestSnapshot`
    before that field read a nominated scene instead. Kept only to reproduce the old answer once.

    "Anchored" is decided flat, from the world's own membership records, never by walking the
    fact tree: a dataset is in the frame when the world is one of its own systems (an adopted
    intrinsic grid or physical space -- in its own space by construction, no edge exists), or
    when a traversable top-level registration into the world sets out from one of its systems
    (the registration *is* membership).

    Three things it deliberately did not promise, all of which the backfill inherits:

    * **Anchored, not drawn.** A dataset registered into a scene's world but never layered was
      still that scene's only anchored dataset -- and the scene's picture may be empty. So a
      seeded default can point at a scene that draws nothing.
    * **Datasets only.** Mesh collections and table datasets were not counted.
    * **No derivation descent.** A dataset placed only through its primary parent's registration
      neither claimed the preview nor blinded it.
    """
    scenes = list(scenes)
    world_ids = {scene.world_id for scene in scenes if scene.world_id}
    registrations = list(models.Transformation.objects.filter(parent__isnull=True, output__in=world_ids))

    residence = residence_map({edge.input_id for edge in registrations if edge.input_id} | world_ids)

    datasets_by_world: dict[int, set[int]] = {}
    for edge in registrations:
        if not is_traversable(edge):
            continue
        if (dataset_id := residence.get(edge.input_id)) is not None:
            datasets_by_world.setdefault(edge.output_id, set()).add(dataset_id)

    by_dataset: dict[int, list[models.Scene]] = {}
    for scene in scenes:
        if not scene.world_id:
            continue
        anchored = set(datasets_by_world.get(scene.world_id, ()))
        if (owner := residence.get(scene.world_id)) is not None:
            anchored.add(owner)
        if len(anchored) == 1:
            by_dataset.setdefault(anchored.pop(), []).append(scene)
    return by_dataset


class Command(BaseCommand):
    """Set `default_scene` on datasets that have none, from the old sole-occupancy derivation."""

    help = "Seed ArrayDataset.default_scene from the sole-occupancy rule latestSnapshot used to derive, so existing thumbnails survive."

    def add_arguments(self, parser) -> None:  # noqa: ANN001 - Django's ArgumentParser
        """Declare the command's options."""
        parser.add_argument("--dry-run", action="store_true", help="Report what would be set without writing anything.")

    def handle(self, *args, **options) -> None:
        """Seed every dataset that has no default and does have a sole-occupancy scene."""
        dry_run = options["dry_run"]

        # Per organization: sole occupancy asks what else is in a picture, and a scene from
        # another organization was never an answer the old field could give either.
        organizations = models.Scene.objects.values_list("organization_id", flat=True).distinct()

        seeded = 0
        for organization_id in organizations:
            scenes = models.Scene.objects.filter(organization_id=organization_id).select_related("world")
            by_dataset = scenes_by_sole_dataset(scenes)
            if not by_dataset:
                continue

            # Only datasets with no nomination yet: a default someone set by hand outranks
            # anything this reproduces.
            candidates = models.ArrayDataset.objects.filter(pk__in=by_dataset, default_scene__isnull=True)

            with transaction.atomic():
                for dataset in candidates:
                    # Newest scene wins, matching what `latestSnapshot` reported when a dataset
                    # was the sole occupant of several: it took the most recent picture across
                    # them, and the newest scene is the closest stable stand-in.
                    scene = max(by_dataset[dataset.pk], key=lambda item: item.pk)
                    self.stdout.write(f"{'would set' if dry_run else 'set'} {dataset.name!r} -> scene {scene.name!r}")
                    if not dry_run:
                        models.ArrayDataset.objects.filter(pk=dataset.pk).update(default_scene=scene)
                    seeded += 1

        remaining = models.ArrayDataset.objects.filter(default_scene__isnull=True).count()
        verb = "would seed" if dry_run else "seeded"
        self.stdout.write(self.style.SUCCESS(f"{verb} {seeded} dataset(s)"))
        self.stdout.write(
            f"{remaining} dataset(s) still have no default scene and will show no thumbnail "
            f"(query them with `array_datasets(filters: {{hasDefaultScene: false}})`). "
            f"When that number is acceptable everywhere this is deployed, delete this command."
        )
