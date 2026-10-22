"""When a container was acquired, as its anchors state it.

The anchors' ``AcquisitionWindow`` spokes are the truth; these derivers keep the envelope of them
on the container (``ArrayDataset.acquisition_start`` / ``acquisition_end``) so a list can filter
and order by it without a join. Imported by the models that declare those columns, so it names
its spoke by label and never imports ``core.models``.
"""

from __future__ import annotations

import datetime

from core.derived import AnchorSet, Derivation, deriver, envelope, project

WINDOW = "core.AcquisitionWindow"


def window(anchors: AnchorSet) -> Derivation[tuple[datetime.datetime, datetime.datetime]]:
    """The span every acquisition window of the container falls in, checked once for both ends."""
    return anchors.memo(WINDOW, lambda: envelope(anchors.statements(WINDOW, lambda spoke: (spoke.start, spoke.end))))


@deriver(sources=(WINDOW,))
def start(anchors: AnchorSet) -> Derivation[datetime.datetime]:
    """The earliest moment any of its anchors says it was acquired at."""
    return project(window(anchors), lambda span: span[0])


@deriver(sources=(WINDOW,))
def end(anchors: AnchorSet) -> Derivation[datetime.datetime]:
    """The latest moment any of its anchors says it was acquired at."""
    return project(window(anchors), lambda span: span[1])
