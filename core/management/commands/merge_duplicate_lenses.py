"""Make the lenses that exist obey the rule new ones are made under: one row per (dataset, selection).

A lens is a deterministic function of what it selects (`core.logic.coordinate_system.create_lens`),
and every dataset has its whole lens from creation. Lenses minted before that rule do not: a scene
bootstrap wrote a fresh unsliced lens each time, `createLens` wrote one per call, and the slices
were stored as the caller spelled them. This converges them, dataset by dataset:

1. **Normalize** the stored slices of every sliced lens to the one spelling
   (`core.logic.coords.normalize_slices`). The selection is unchanged. Where the lens' own edge
   back to its dataset -- the one from the lens' system into the intrinsic system -- disagrees
   with what the normalized slices derive (a slice stored with a negative start wrote a negative
   translation), that edge's parameters are rewritten. No edge changes systems.
2. **Backfill** the whole lens for every dataset that has none.
3. **Merge** duplicates onto the lowest pk. `Layer.lens` and `ChartLayer.lens` are repointed. An
   unsliced duplicate shares the intrinsic system and is simply deleted. A sliced duplicate owns a
   system: it is deleted, with its edge and its system, only when nothing else is attached to that
   system -- no other resident, no scene or chart over it, no edge besides its own, nothing that
   PROTECTs it. Otherwise it is left in place and reported, because moving what is attached would
   be a decision this command is not entitled to.

Re-runnable: it finds what is still owed and does nothing when nothing is. Listed in the image's
setup, so it runs inside `migrate` for every build, before the release serves -- and no row spelled
the old way is still there to escape the lookup `createLens` makes. What it leaves in place is
printed, not failed on: the installer must not be stopped by a lens someone built a space around.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from chart.models import ChartLayer
from core import enums, models
from core.logic import compositions as compositions_logic
from core.logic import coordinate_system as coordinate_system_logic
from core.logic import coords as coords_logic
from core.logic import graph as graph_logic


def _key(slices: list[dict[str, int | str]]) -> str:
    """The grouping key of a normalized selection: its JSON, stable across runs."""
    return json.dumps(slices, sort_keys=True)


def _own_edges(lens: models.Lens) -> Iterable[models.Transformation]:
    """The top-level edge(s) from this lens' own system into its dataset's intrinsic system."""
    if lens.coordinate_system_id is None or lens.coordinate_system_id == lens.dataset.coordinate_system_id:
        return ()
    return models.Transformation.objects.filter(input_id=lens.coordinate_system_id, output_id=lens.dataset.coordinate_system_id, parent__isnull=True)


def _system_is_private_to(lens: models.Lens) -> str | None:
    """Why this sliced lens' system cannot be deleted with it, or None when it can.

    The same three questions `deleteCoordinateSystem` asks -- residents, compositions, edges --
    plus the two PROTECT relations a delete would trip over.
    """
    system = lens.coordinate_system
    if system is None or system.pk == lens.dataset.coordinate_system_id:
        return "it lives in the intrinsic system"
    for relation in graph_logic.RESIDENT_RELATIONS:
        residents = getattr(system, relation).all()
        if relation == "lenses":
            residents = residents.exclude(pk=lens.pk)
        if residents.exists():
            return f"other {relation.replace('_', ' ')} live in its system"
    try:
        compositions_logic.assert_no_composition_over(system)
    except ValueError:
        return "a scene or chart is laid out over its system"
    own = [edge.pk for edge in _own_edges(lens)]
    if models.Transformation.objects.filter(Q(input=system) | Q(output=system), parent__isnull=True).exclude(pk__in=own).exists():
        return "its system has registration edges besides its own"
    if system.fields_of.exists():
        return "its system is the field of a transformation"
    if system.annotation_bbox_frames.exists():
        return "annotation boxes are expressed in its system"
    return None


class Command(BaseCommand):
    """Normalize, backfill and merge lenses so that one selection is one row."""

    help = "Normalize stored lens slices, give every dataset its whole lens, and merge duplicate lenses onto one row."

    dry_run: bool = False
    counts: dict[str, int]

    def add_arguments(self, parser) -> None:  # noqa: ANN001 - Django's ArgumentParser
        """Declare the command's options."""
        parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")

    def handle(self, *args, **options) -> None:
        """Converge every dataset's lenses, one transaction per dataset."""
        self.dry_run = options["dry_run"]
        self.counts = {"normalized": 0, "edges_repaired": 0, "backfilled": 0, "merged_unsliced": 0, "merged_sliced": 0, "left": 0}

        datasets = models.ArrayDataset.objects.filter(coordinate_system__isnull=False).order_by("pk").select_related("coordinate_system")
        for dataset in datasets.iterator():
            if dataset.data_arrays.order_by("level").first() is None:
                continue
            with transaction.atomic():
                self._converge(dataset)
                if self.dry_run:
                    transaction.set_rollback(True)

        verb = "would " if self.dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}normalized {self.counts['normalized']} lens(es), {verb}repaired {self.counts['edges_repaired']} edge(s), "
                f"{verb}backfilled {self.counts['backfilled']} whole lens(es), {verb}merged {self.counts['merged_unsliced']} unsliced and "
                f"{self.counts['merged_sliced']} sliced duplicate(s)"
            )
        )
        if self.counts["left"]:
            self.stdout.write(f"{self.counts['left']} duplicate sliced lens(es) left in place because something else is attached to their system; each is named above.")

    # --- one dataset ---------------------------------------------------------------

    def _converge(self, dataset: models.ArrayDataset) -> None:
        shape, axis_names = dataset.shape_list, dataset.axis_names
        lenses = list(dataset.lenses.order_by("pk").select_related("coordinate_system"))

        # 1. The one spelling. A row whose slices resolve to nothing narrower than the array is
        #    the whole lens under another name and joins that group below.
        groups: dict[str, list[models.Lens]] = {}
        for lens in lenses:
            try:
                normalized = coords_logic.normalize_slices(shape, axis_names, lens.slices_list)
            except ValueError as error:
                self.stdout.write(f"lens {lens.pk} of dataset '{dataset.name}' ({dataset.pk}) left as is: its slices do not resolve ({error})")
                continue
            if normalized and normalized != lens.slices:
                self._normalize(lens, normalized)
            elif not normalized and lens.coordinate_system_id is None:
                # Written before every lens had a system: an unsliced lens lives in the grid.
                self.counts["normalized"] += 1
                self.stdout.write(f"{'would place' if self.dry_run else 'placed'} lens {lens.pk} in the intrinsic system it had no row for")
                lens.coordinate_system_id = dataset.coordinate_system_id
                models.Lens.objects.filter(pk=lens.pk).update(coordinate_system_id=dataset.coordinate_system_id)
            groups.setdefault(_key(normalized), []).append(lens)

        # 2. The whole lens, whatever that group holds. Reads `whole_lens` so the row is the one
        #    `createArrayDataset` would have made, and so a dry run still names it.
        if not any(lens.coordinate_system_id == dataset.coordinate_system_id and not lens.slices for lens in groups.get(_key([]), [])):
            self.counts["backfilled"] += 1
            self.stdout.write(f"{'would backfill' if self.dry_run else 'backfilled'} the whole lens of dataset '{dataset.name}' ({dataset.pk})")
        whole = coordinate_system_logic.whole_lens(dataset)
        whole_group = [lens for lens in groups.pop(_key([]), []) if lens.pk != whole.pk]
        for duplicate in whole_group:
            self._merge(duplicate, into=whole)

        # 3. Everything else: lowest pk keeps.
        for group in groups.values():
            keeper, *duplicates = group
            for duplicate in duplicates:
                self._merge(duplicate, into=keeper)

    def _normalize(self, lens: models.Lens, normalized: list[dict[str, int | str]]) -> None:
        self.counts["normalized"] += 1
        self.stdout.write(f"{'would normalize' if self.dry_run else 'normalized'} lens {lens.pk}: {lens.slices} -> {normalized}")
        lens.slices = normalized
        models.Lens.objects.filter(pk=lens.pk).update(slices=normalized)

        kind, params = coords_logic.lens_to_parent(lens.dataset.axis_names, lens.slices_list)
        for edge in _own_edges(lens):
            if kind != enums.TransformKindChoices.TRANSLATION.value or edge.kind != kind:
                # A stepped lens is a SEQUENCE of child rows; its spelling never changed the
                # scale, and its starts are left to a later pass if one is ever needed.
                continue
            if edge.params == params:
                continue
            self.counts["edges_repaired"] += 1
            self.stdout.write(f"{'would repair' if self.dry_run else 'repaired'} edge {edge.pk} of lens {lens.pk} (its own, into intrinsic): translation {edge.params.get('translation')} -> {params['translation']}")
            models.Transformation.objects.filter(pk=edge.pk).update(params=params)

    def _merge(self, duplicate: models.Lens, *, into: models.Lens) -> None:
        sliced = duplicate.coordinate_system_id not in (None, duplicate.dataset.coordinate_system_id)
        if sliced:
            reason = _system_is_private_to(duplicate)
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

        system = duplicate.coordinate_system
        duplicate.delete()
        if sliced:
            self.counts["merged_sliced"] += 1
            # Its edge, then its system; the axes cascade with the system.
            models.Transformation.objects.filter(Q(input=system) | Q(output=system)).delete()
            if system is not None:
                system.delete()
        else:
            self.counts["merged_unsliced"] += 1
