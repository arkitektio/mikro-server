"""The value rules that repeat across inputs: colours, alphas, positive magnitudes.

Plain functions rather than pydantic types, for two reasons. A ``@field_validator`` calling
one of these keeps the field's declared type exactly as the SDL already publishes it -- an
``Annotated[float, Field(ge=0)]`` would too, but it would carry pydantic's own wording, and
in this codebase a resolver's exception reaches the client as ``errors[0].message`` verbatim,
so the message *is* the API contract. And the same rule has to hold at two altitudes in
places (see :mod:`core.inputs.coords`), which a shared function does and a type annotation
does not.

Every one of these rejects only what cannot be meaningful. A negative scale factor is a
mirrored axis, an inverted pair of box corners normalises, and an unusual-but-finite number
is somebody's real measurement -- none of them belong here.
"""

from typing import Annotated

from pydantic import AfterValidator

#: An RGBA colour is four components. Named so the several colour checks cannot drift apart.
_RGBA_LENGTH = 4


def assert_rgba(color: list, *, field: str, maximum: float | None = None) -> None:
    """Reject a colour that is not four components, and optionally one out of range.

    ``maximum`` is passed only where the component range is actually written down --
    ``255`` for the integer colours. Where it is not, the length is still checkable:
    "RGBA" says four components whatever scale they are on.
    """
    if len(color) != _RGBA_LENGTH:
        raise ValueError(f"`{field}` is an RGBA colour, so it takes exactly {_RGBA_LENGTH} components (red, green, blue, alpha), but got {len(color)} ({color}).")

    if maximum is not None:
        out_of_range = [component for component in color if not 0 <= component <= maximum]
        if out_of_range:
            raise ValueError(f"`{field}`'s components run from 0 to {maximum}, but got {out_of_range} in {color}.")


def assert_alpha(value: float, *, field: str) -> None:
    """Reject an opacity outside 0..1, the range the field and its column both document."""
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"`{field}` is an alpha for alpha-over compositing, so it runs from 0 (transparent) to 1 (opaque), but got {value}.")


def _validated_alpha(value: float) -> float:
    assert_alpha(value, field="opacity")
    return value


#: A layer alpha, checked. Ten input models carry an ``opacity`` and every one of them
#: documents it as 0..1 -- in the field's description *and* in the column's help text -- so
#: the rule is written once here and spelled ``opacity: Alpha | None`` at each of them,
#: rather than as ten copies of one validator free to drift apart. The GraphQL type is
#: unaffected: the strawberry inputs declare their own fields, and this annotation is read
#: only on the way into the pydantic model.
Alpha = Annotated[float, AfterValidator(_validated_alpha)]


def assert_contrast_limits(clim_min: float | None, clim_max: float | None) -> None:
    """Reject a contrast window whose lower limit is above its upper one.

    Ordering only, and deliberately no range. A contrast limit is in the data's own
    intensity units -- the actual values of the array, not a normalized 0..1 fraction --
    so there is no interval to hold it to, and any finite number is somebody's real
    detector reading.

    An inverted pair is not a dark image, it is an empty one: every intensity falls
    outside the window. `invert` is how a client asks for the mapping to run backwards.
    """
    if clim_min is not None and clim_max is not None and clim_min > clim_max:
        raise ValueError(f"`climMin` is the lower contrast limit, so it cannot exceed `climMax`, but got {clim_min} > {clim_max}. Use `invert` to run the mapping backwards.")


def assert_positive(value: float, *, field: str, because: str) -> None:
    """Reject a magnitude of zero or less, where zero is a degenerate rather than a limit."""
    if value <= 0:
        raise ValueError(f"`{field}` must be greater than zero -- {because} -- but got {value}.")


def assert_not_negative(value: float, *, field: str, because: str) -> None:
    """Reject a negative magnitude, where zero is still a meaningful value."""
    if value < 0:
        raise ValueError(f"`{field}` cannot be negative -- {because} -- but got {value}.")


#: The fewest vertices each shape kind can be drawn from, where its encoding says so. A
#: kind absent from this table takes the default of one: MULTI_POINT and POINT carry no
#: vertex-count rule worth imposing. These are minimums, never exact counts -- extra
#: vertices are not nonsensical, and the two-corner families read `vectors[:2]` and ignore
#: the rest.
#:
#: **Every key must be an `AnnotationKindChoices` value**, and a guard test in
#: `tests/test_architecture.py` holds it to that. It used to carry six more --
#: `spectral_rectangle`, `temporal_rectangle`, `spectral_cube`, `temporal_cube`,
#: `hypercube`, `spectral_hypercube` -- from the ROI-kind vocabulary that stopped backing
#: `Annotation.kind`. No annotation could ever be drawn as one, so no lookup could ever
#: reach them.
_MINIMUM_VERTICES: dict[str, int] = {
    # The two-corner families: a rectangle, a cuboid, and every round kind, are all stored
    # as the two opposite corners of a bounding box, so one corner does not describe one of
    # them -- it silently yields a box of all-None bounds.
    "ellipse": 2,
    "circle": 2,
    "sphere": 2,
    "ellipsoid": 2,
    "rectangle": 2,
    "cube": 2,
    # A line runs between two points, an open path needs two to have a direction, and a
    # closed polygon needs three to enclose anything.
    "line": 2,
    "path": 2,
    "polygon": 3,
}


def assert_shape_vectors(vectors: list, *, kind: str | None) -> None:
    """Reject a shape whose vertices cannot describe it.

    Two rules, both about geometry that is not merely unusual but unreadable:

    **Rectangular.** Every vertex must have the same number of components. ``aabb`` takes
    its dimension from the *first* point, so a later vertex that is longer raises an
    ``IndexError`` -- a 500, not an error -- and one that is shorter silently loses its
    trailing components from the box. The vector scalars (``ThreeDVector``,
    ``FiveDVector``) are pass-through ``NewType``s and enforce nothing, so this is the
    only place the shape of the geometry is checked at all.

    **Enough vertices for the kind.** Only where the kind's encoding says a number; see
    :data:`_MINIMUM_VERTICES`.

    An **empty** ``vectors`` is left alone, and is the one case that looks like it should
    be caught here. It is not a malformed shape but a declared absence of geometry:
    ``bbox_along_chain`` answers ``None`` for it by design, and ``nearestAnnotations``
    excludes such a shape on purpose ("nowhere, not near"). So the count rule applies to a
    shape that *has* vertices -- one corner does not make a rectangle -- and says nothing
    about a shape that has none.

    Deliberately also *not* a check of the vertex width against the drawing space's axis
    count: a two-dimensional shape drawn into a three-dimensional collection is an
    ordinary thing to want, and its `coordinates` pins are how it says which slice it is on.
    """
    if not vectors:
        return

    widths = {len(vector) for vector in vectors}
    if len(widths) > 1:
        raise ValueError(f"Every vertex of one shape has the same number of components, but `vectors` mixes widths {sorted(widths)}. The bounding box is taken at the width of the first vertex, so the others would be truncated or would overrun it.")

    minimum = _MINIMUM_VERTICES.get(kind or "", 1)
    if len(vectors) < minimum:
        raise ValueError(f"A {kind} is drawn from at least {minimum} vertices, but `vectors` has {len(vectors)}.")
