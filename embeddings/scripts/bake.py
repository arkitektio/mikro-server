"""Bake the embedding model's weights into the image, and check they are the release's.

The image's part of the `embeddings` app, kept with it: what the Dockerfile runs is here, not
written out in the Dockerfile. Two steps, on purpose:

``bake MODEL``
    Downloads ``MODEL`` from Hugging Face into the directory the service loads its weights
    from, and writes the model's id beside them. Needs nothing of the service's source, so it
    runs before the source is copied and a source change does not download the weights again.

``check``
    Fails unless the baked weights are those of ``embeddings.engine.MODEL``. The model is a
    constant of the release, not a build option: an image baked with another one would refuse
    to embed at its start, so the build refuses it first. Runs once the source is there.

    python embeddings/scripts/bake.py bake minishlab/potion-base-8M
    python embeddings/scripts/bake.py check
"""

from __future__ import annotations

import os
import sys

#: Where the service loads its weights from (``embeddings.engine.MODEL_PATH``). Said again
#: here because ``bake`` runs before the service's source is in the image; ``check`` holds the
#: two to each other.
TARGET = "/opt/models/embeddings"
#: The file naming the model the weights are of (``embeddings.engine.MODEL_ID_FILENAME``).
STAMP = "MODEL_ID"


def bake(model: str) -> None:
    """Download ``model``'s weights into :data:`TARGET` and say whose they are."""
    from model2vec import StaticModel

    StaticModel.from_pretrained(model).save_pretrained(TARGET)
    with open(os.path.join(TARGET, STAMP), "w", encoding="utf-8") as stamp:
        stamp.write(model)


def check() -> None:
    """Refuse an image whose baked weights are not the release's model."""
    # The source is copied to the working directory, which a script run by path is not on.
    sys.path.insert(0, os.getcwd())
    from embeddings import engine

    assert (engine.MODEL_PATH, engine.MODEL_ID_FILENAME) == (TARGET, STAMP), f"this script bakes into {TARGET}/{STAMP}, but the service reads {engine.MODEL_PATH}/{engine.MODEL_ID_FILENAME}"
    with open(os.path.join(TARGET, STAMP), encoding="utf-8") as stamp:
        baked = stamp.read().strip()
    assert baked == engine.MODEL, f"the image bakes {baked!r} but embeddings.engine.MODEL is {engine.MODEL!r}"


def main(arguments: list[str]) -> int:
    """Run one step; the exit code is its answer."""
    match arguments:
        case ["bake", model]:
            bake(model)
        case ["check"]:
            check()
        case _:
            print(__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
