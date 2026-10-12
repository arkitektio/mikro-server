"""Make the lenses that exist obey the rule new ones are made under: one row per (container, selection).

A lens is a deterministic function of what it selects (`core.logic.coordinate_system`), and
every container has its whole lens from creation. What this catches is the row that escaped
that rule: a selection stored under a spelling the normalizer no longer produces, a container
of any kind whose whole lens was never written, and the duplicate the database could not refuse
because it was written around the constraint. It converges them, container by container:

1. **Normalize** the stored selection of every lens to the one spelling
   (`core.logic.coords.normalize_slices` / `normalize_windows`). The selection is unchanged.
   Where an array lens' own edge back to its dataset -- the one from the lens' system into the
   intrinsic system -- disagrees with what the normalized slices derive (a slice stored with a
   negative start wrote a negative translation), that edge's parameters are rewritten. No edge
   changes systems.
2. **Backfill** the whole lens for every container of every kind that has none.
3. **Merge** array lenses that duplicate one selection onto the lowest pk. `Layer.lens` and
   `ChartLayer.lens` are repointed. An unsliced duplicate shares the intrinsic system and is
   simply deleted. A sliced duplicate owns a system: it is deleted, with its edge and its
   system, only when nothing else is attached to that system -- no other resident, no scene or
   chart over it, no edge besides its own, nothing that PROTECTs it. Otherwise it is left in
   place and reported. The database refuses a duplicate since the lens tables were split, so
   this step finds work only on rows written around it by hand.
4. **Materialize** the spec of every lens whose stored one is not what its container and
   selection denote (`Lens.derive_spec`): the rows written before lenses stated a spec, the
   ones a rolled-back release wrote without one, and every row after the rule itself changes.

Re-runnable: it finds what is still owed and does nothing when nothing is. Listed in the image's
setup, so it runs inside `migrate` for every build, before the release serves -- and no row spelled
the old way is still there to escape the lookup `createLens` makes. What it leaves in place is
printed, not failed on: the installer must not be stopped by a lens someone built a space around.
"""

from __future__ import annotations

import json
from typing import cast

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from chart.models import ChartLayer
from core import enums, models
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import coords as coords_logic


def _key(slices: list[dict[str, int | str]]) -> str:
    """The grouping key of a normalized selection: its JSON, stable across runs."""
    return json.dumps(slices, sort_keys=True)


class Command(BaseCommand):
    """Normalize, backfill and merge lenses so that one selection is one row."""

    help = "Normalize stored lens selections, give every container its whole lens, and merge duplicate array lenses onto one row."

    dry_run: bool = False
    counts: dict[str, int]

    def add_arguments(self, parser) -> None:  # noqa: ANN001 - Django's ArgumentParser
        """Declare the command's options."""
        parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")

    def handle(self, *args, **options) -> None:
        """Converge every container's lenses, one transaction per container."""
        self.dry_run = options["dry_run"]
        self.counts = {"normalized": 0, "edges_repaired": 0, "backfilled": 0, "merged_unsliced": 0, "merged_sliced": 0, "left": 0, "specified": 0}

        datasets = models.ArrayDataset.objects.filter(coordinate_system__isnull=False).order_by("pk").select_related("coordinate_system")
        for dataset in datasets.iterator():
            if dataset.data_arrays.order_by("level").first() is None:
                continue
            with transaction.atomic():
                self._converge(dataset)
                if self.dry_run:
                    transaction.set_rollback(True)

        # Every windowed kind the registry knows, so a seventh kind joins this job unasked.
        for noun, containers in (
            (str(container_model._meta.verbose_name), container_model.objects.filter(coordinate_system__isnull=False))
            for container_model, lens_model in models.Lens.KIND_BY_CONTAINER.items()
            if lens_model is not models.ArrayLens
        ):
            for container in containers.order_by("pk").iterator():
                with transaction.atomic():
                    self._converge_windowed(noun, container)
                    if self.dry_run:
                        transaction.set_rollback(True)

        self._specify()

        verb = "would " if self.dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}normalized {self.counts['normalized']} lens(es), {verb}repaired {self.counts['edges_repaired']} edge(s), "
                f"{verb}backfilled {self.counts['backfilled']} whole lens(es), {verb}merged {self.counts['merged_unsliced']} unsliced and "
                f"{self.counts['merged_sliced']} sliced duplicate(s), {verb}stated the spec of {self.counts['specified']} lens(es)"
            )
        )
        if self.counts["left"]:
            self.stdout.write(f"{self.counts['left']} duplicate sliced lens(es) left in place because something else is attached to their system; each is named above.")

    # --- what every lens is --------------------------------------------------------

    def _specify(self) -> None:
        """Give every lens the spec its container and selection denote. After the convergence, so it reads the normalized selections."""
        for lens in models.Lens.objects.with_sources().order_by("pk").iterator(chunk_size=500):
            spec = [member.value for member in lens.derive_spec()]
            if spec == lens.stored_spec:
                continue
            self.counts["specified"] += 1
            if not self.dry_run:
                models.Lens.objects.filter(pk=lens.pk).update(stored_spec=spec)

    # --- a container that is not an array ------------------------------------------

    def _converge_windowed(self, noun: str, container: object) -> None:
        kind = coordinate_system_logic.lens_kind_for(container)
        axis_names = [axis.name for axis in container.coordinate_system.axes.all()]  # type: ignore[attr-defined]
        if not kind.objects.filter(**{kind.CONTAINER_FIELD: container}, windows=[]).exists():
            self.counts["backfilled"] += 1
            self.stdout.write(f"{'would backfill' if self.dry_run else 'backfilled'} the whole lens of {noun} {container.pk}")  # type: ignore[attr-defined]
        coordinate_system_logic.whole_lens(container)
        for lens in kind.objects.filter(**{kind.CONTAINER_FIELD: container}).order_by("pk"):
            try:
                normalized = coords_logic.normalize_windows(axis_names, lens.windows_list)
            except ValueError as error:
                self.stdout.write(f"lens {lens.pk} of {noun} {container.pk} left as is: its windows do not resolve ({error})")  # type: ignore[attr-defined]
                continue
            if normalized != lens.windows:
                self.counts["normalized"] += 1
                self.stdout.write(f"{'would normalize' if self.dry_run else 'normalized'} lens {lens.pk}: {lens.windows} -> {normalized}")
                kind.objects.filter(pk=lens.pk).update(windows=normalized)

    # --- one dataset ---------------------------------------------------------------

    def _converge(self, dataset: models.ArrayDataset) -> None:
        shape, axis_names = dataset.shape_list, dataset.axis_names
        lenses = list(models.ArrayLens.objects.filter(dataset=dataset).order_by("pk").select_related("coordinate_system"))

        # 1. The one spelling, computed for every row before anything is written: the database
        #    holds one row per spelling, so a row is rewritten only once the rows it would then
        #    duplicate are folded into it.
        groups: dict[str, list[tuple[models.ArrayLens, list]]] = {}
        for lens in lenses:
            try:
                normalized = coords_logic.normalize_slices(shape, axis_names, lens.slices_list)
            except ValueError as error:
                self.stdout.write(f"lens {lens.pk} of dataset '{dataset.name}' ({dataset.pk}) left as is: its slices do not resolve ({error})")
                continue
            groups.setdefault(_key(normalized), []).append((lens, normalized))

        # 2. The whole lens, whatever that group holds. Reads `whole_lens` so the row is the one
        #    `createArrayDataset` would have made, and so a dry run still names it.
        if not any(lens.coordinate_system_id == dataset.coordinate_system_id and not lens.slices for lens, _ in groups.get(_key([]), [])):
            self.counts["backfilled"] += 1
            self.stdout.write(f"{'would backfill' if self.dry_run else 'backfilled'} the whole lens of dataset '{dataset.name}' ({dataset.pk})")
        whole = cast(models.ArrayLens, coordinate_system_logic.whole_lens(dataset))
        for duplicate, _ in groups.pop(_key([]), []):
            if duplicate.pk != whole.pk:
                self._merge(duplicate, into=whole)

        # 3. Everything else: lowest pk keeps, the rest fold into it, and only then is the
        #    keeper's spelling rewritten -- unless a row it could not fold still holds it.
        for members in groups.values():
            (keeper, normalized), *duplicates = members
            for duplicate, _ in duplicates:
                self._merge(duplicate, into=keeper)
            if normalized != keeper.slices:
                if models.ArrayLens.objects.filter(dataset=dataset, slices=normalized).exclude(pk=keeper.pk).exists():
                    self.stdout.write(f"lens {keeper.pk} keeps its spelling {keeper.slices}: the normalized one {normalized} is held by a duplicate left in place")
                    continue
                self._normalize(keeper, normalized)

    def _normalize(self, lens: models.ArrayLens, normalized: list[dict[str, int | str]]) -> None:
        self.counts["normalized"] += 1
        self.stdout.write(f"{'would normalize' if self.dry_run else 'normalized'} lens {lens.pk}: {lens.slices} -> {normalized}")
        lens.slices = normalized
        models.ArrayLens.objects.filter(pk=lens.pk).update(slices=normalized)

        kind, params = coords_logic.lens_to_parent(lens.dataset.axis_names, lens.slices_list)
        for edge in coordinate_system_logic.lens_own_edges(lens):
            if kind != enums.TransformKindChoices.TRANSLATION.value or edge.kind != kind:
                # A stepped lens is a SEQUENCE of child rows; its spelling never changed the
                # scale, and its starts are left to a later pass if one is ever needed.
                continue
            if edge.params == params:
                continue
            self.counts["edges_repaired"] += 1
            self.stdout.write(f"{'would repair' if self.dry_run else 'repaired'} edge {edge.pk} of lens {lens.pk} (its own, into intrinsic): translation {edge.params.get('translation')} -> {params['translation']}")
            models.Transformation.objects.filter(pk=edge.pk).update(params=params)

    def _merge(self, duplicate: models.ArrayLens, *, into: models.ArrayLens) -> None:
        sliced = duplicate.coordinate_system_id != duplicate.dataset.coordinate_system_id
        if sliced:
            reason = coordinate_system_logic.lens_space_is_shared_because(duplicate)
            if reason is not None:
                self.counts["left"] += 1
                self.stdout.write(f"lens {duplicate.pk} duplicates lens {into.pk} but is left in place: {reason} (system {duplicate.coordinate_system_id})")
                return

        layers = models.Layer.objects.filter(lens=duplicate).count()
        chart_layers = ChartLayer.objects.filter(lens=duplicate).count()
        verb = "would merge" if self.dry_run else "merged"
        self.stdout.write(f"{verb} lens {duplicate.pk} into lens {into.pk} ({layers} layer(s), {chart_layers} chart layer(s) repointed)")
        models.Layer.objects.filter(lens=duplicate).update(lens=into)
        ChartLayer.objects.filter(lens=duplicate).update(lens=into)

        # The row, and for a sliced one its edge and its system, as `deleteLens` takes them.
        coordinate_system_logic.delete_lens(duplicate)
        self.counts["merged_sliced" if sliced else "merged_unsliced"] += 1
