"""Handing a stored vector to the API as one opaque, self-describing string.

A vector is meaningless without the model that produced it -- two lists of 256 floats from
two different models are not comparable, and a client that cached one across a model change
would be quietly comparing noise. So the field is not a list of floats: it is
``<model id>:<floats>``, and the descriptor is part of the value rather than a sibling field
a client may forget to read.

The vector itself stays read-only. Nothing parses this back into a row; it is published so a
client can group, cluster or cache results it already has, not so it can write vectors.
"""

from __future__ import annotations

from typing import Any, NewType

import strawberry

#: What separates the model id from the numbers. A model id never contains it.
DESCRIPTOR_SEPARATOR = ":"

Embedding = NewType("Embedding", str)

scalar_map: dict[Any, Any] = {
    Embedding: strawberry.scalar(
        name="Embedding",
        description=(
            "A stored vector, as `<model id>:<comma-separated floats>` -- e.g. "
            "`potion-base-8M:0.0123,-0.0456,...`. The model id is part of the value because "
            "vectors from different models are not comparable. Null when the row has no "
            "vector yet (it carries no text, or indexing has not caught up with it)."
        ),
        serialize=lambda value: value,
        parse_value=lambda value: value,
    )
}


def format_embedding(vector: Any, model_id: str | None) -> Embedding | None:
    """``<model id>:<floats>``, or ``None`` when there is no vector to describe."""
    if vector is None or not model_id:
        return None
    # `repr` of a float round-trips exactly and is shorter than a fixed format for the
    # values a unit vector holds; a client that parses this back gets the stored vector.
    return Embedding(f"{model_id}{DESCRIPTOR_SEPARATOR}{','.join(repr(float(component)) for component in vector)}")


def embedding_of(instance: Any) -> Embedding | None:
    """The formatted vector of a row carrying :class:`embeddings.models.EmbeddedDescriptionMixin`."""
    return format_embedding(getattr(instance, "embedding", None), getattr(instance, "embedding_model", ""))
