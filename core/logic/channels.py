"""What a channel's recorded name says about how to draw it.

Ingest records a :class:`~core.models.ChannelLabel` per channel -- "DAPI", "GFP",
"Brightfield", "Alexa 647", "Red" -- and until now the bootstrap used it as a string and
nothing more: it became ``Layer.name`` and the hue came from an index cycle, so a DAPI
channel was as likely to come back green as blue. The name is evidence, and this module is
where it is read.

**Only recorded facts, never shape.** That is the standing rule of the bootstrap and the
reason RGB inference was deleted once already: a flat three-channel array is a three-marker
fluorescence acquisition far more often than a photograph, and the two are indistinguishable
by shape. Nothing here looks at a shape. Every answer comes from something a converter
actually wrote down -- a label, or (in :mod:`core.logic.file_link`) the media type of the
file the arrays were read out of.

**And every answer is a default.** A wrong hue costs one ``updateLayer``; the guess is made
once, at bootstrap, and is never consulted again. That is what makes a dictionary of
fluorophore names acceptable here where it would not be in the coordinate graph: it decides
a colour, not a fact about where data sits.

Unrecognized names fall through to ``None`` and the caller keeps its own default -- the
index-cycled hues, which is exactly the behaviour every channel had before this module
existed.
"""

import re

from core import enums


#: How a fluorophore's name maps to the hue it is conventionally drawn in. Keys are
#: normalized (see :func:`normalize`), so "Alexa Fluor 647" and "alexa-fluor-647" are one
#: entry. Matched as a whole label first and then as a substring, longest key first, so
#: "farred" beats the "red" inside it and "texasred" is not read as a bare red.
#:
#: Emission colour, not excitation: DAPI is excited in the UV and drawn blue.
_FLUOROPHORE_COLORMAPS: dict[str, "enums.ColorMap"] = {
    # Blue emitters.
    "dapi": enums.ColorMap.BLUE,
    "hoechst": enums.ColorMap.BLUE,
    "bfp": enums.ColorMap.BLUE,
    "ebfp": enums.ColorMap.BLUE,
    "pacificblue": enums.ColorMap.BLUE,
    "blue": enums.ColorMap.BLUE,
    # Cyan emitters.
    "cfp": enums.ColorMap.CYAN,
    "ecfp": enums.ColorMap.CYAN,
    "cerulean": enums.ColorMap.CYAN,
    "cyan": enums.ColorMap.CYAN,
    # Green emitters.
    "gfp": enums.ColorMap.GREEN,
    "egfp": enums.ColorMap.GREEN,
    "fitc": enums.ColorMap.GREEN,
    "alexa488": enums.ColorMap.GREEN,
    "atto488": enums.ColorMap.GREEN,
    "green": enums.ColorMap.GREEN,
    # Yellow emitters.
    "yfp": enums.ColorMap.YELLOW,
    "eyfp": enums.ColorMap.YELLOW,
    "venus": enums.ColorMap.YELLOW,
    "citrine": enums.ColorMap.YELLOW,
    "yellow": enums.ColorMap.YELLOW,
    # Orange / red emitters.
    "dsred": enums.ColorMap.ORANGE,
    "tdtomato": enums.ColorMap.ORANGE,
    "mscarlet": enums.ColorMap.RED,
    "mcherry": enums.ColorMap.RED,
    "rfp": enums.ColorMap.RED,
    "tritc": enums.ColorMap.RED,
    "cy3": enums.ColorMap.RED,
    "texasred": enums.ColorMap.RED,
    "propidiumiodide": enums.ColorMap.RED,
    "red": enums.ColorMap.RED,
    # Far red, drawn magenta: the eye has nothing to see past ~700 nm, and magenta is what
    # every microscopy viewer has always shown a far-red channel in.
    "cy5": enums.ColorMap.MAGENTA,
    "cy7": enums.ColorMap.MAGENTA,
    "alexa647": enums.ColorMap.MAGENTA,
    "atto647n": enums.ColorMap.MAGENTA,
    "draq5": enums.ColorMap.MAGENTA,
    "farred": enums.ColorMap.MAGENTA,
    "magenta": enums.ColorMap.MAGENTA,
}

#: The same question answered from a number: the laser line or filter centre a converter
#: wrote into the channel name ("640", "ch_488", "Alexa 555"), and the colour a channel
#: excited there is conventionally drawn in. Bands, not exact lines, because every facility
#: writes its own nominal value -- 633, 638, 640 and 647 are all "the far-red channel".
_WAVELENGTH_HUES: list[tuple[int, int, "enums.ColorMap"]] = [
    (350, 425, enums.ColorMap.BLUE),
    (425, 470, enums.ColorMap.CYAN),
    (470, 505, enums.ColorMap.GREEN),
    (505, 545, enums.ColorMap.YELLOW),
    (545, 605, enums.ColorMap.RED),
    (605, 800, enums.ColorMap.MAGENTA),
]

#: Names that mean transmitted light rather than a fluorophore. Split by how safely they can
#: be matched: the long ones are unambiguous anywhere in a label ("H2B-GFP brightfield" is a
#: brightfield channel), while "bf", "tl" and "ph" are two letters and would fire inside any
#: number of real fluorophore names, so they have to be the whole label.
_TRANSMITTED_SUBSTRINGS = ("brightfield", "transmitted", "phasecontrast", "differentialinterference", "labelfree", "widefieldtrans")
_TRANSMITTED_EXACT = frozenset({"bf", "tl", "trans", "dic", "phase", "ph", "brightfield", "transmission"})

#: What the three components of a colour photograph are called, whatever the converter's
#: taste. Only ever consulted as a complete set (see :func:`rgb_components`).
_RGB_COMPONENTS: dict[str, tuple[str, ...]] = {
    "red": ("red", "r"),
    "green": ("green", "g"),
    "blue": ("blue", "b"),
}


def normalize(label: str | None) -> str:
    """A label reduced to the part that carries meaning: lowercase alphanumerics.

    "Alexa Fluor 647", "alexa-fluor-647" and "AlexaFluor647" are one name written three
    ways, and the difference between them is punctuation nobody chose deliberately.
    """
    return re.sub(r"[^a-z0-9]", "", (label or "").lower())


def _wavelength_hue(normalized: str) -> "enums.ColorMap | None":
    """The hue implied by a wavelength written into the name, if there is one.

    Three-digit runs only, and maximal ones: "Hoechst 33342" carries no wavelength, and
    reading the "333" out of the middle of its catalogue number would draw it blue for a
    reason that is a coincidence. (Blue is the right answer there -- through the name, which
    is checked first.)
    """
    for run in re.findall(r"\d+", normalized):
        if len(run) != 3:
            continue
        value = int(run)
        for low, high, colormap in _WAVELENGTH_HUES:
            if low <= value < high:
                return colormap
    return None


def colormap_for(label: str | None) -> "enums.ColorMap | None":
    """The hue a channel with this name should be drawn in, or None to leave it to the caller.

    Three passes, narrowing: the whole label as a name, then a name appearing inside a longer
    one ("H2B-GFP", "GFP (488)"), then a wavelength. None is a real answer and the common one
    -- "channel 2" says nothing about colour, and the caller's index cycle is a better default
    than a made-up one.
    """
    normalized = normalize(label)
    if not normalized:
        return None

    exact = _FLUOROPHORE_COLORMAPS.get(normalized)
    if exact is not None:
        return exact

    # Longest first: "farred" and "texasred" both contain "red", and the specific name is the
    # one the writer meant.
    for name in sorted(_FLUOROPHORE_COLORMAPS, key=len, reverse=True):
        if name in normalized:
            return _FLUOROPHORE_COLORMAPS[name]

    return _wavelength_hue(normalized)


def is_transmitted(label: str | None) -> bool:
    """Whether this names a transmitted-light channel: brightfield, DIC, phase contrast.

    It is worth telling apart from a fluorophore for one reason, and it is not the hue.
    Fluorescence channels are *summed* -- two markers glowing at once make a brighter pixel,
    which is what ADDITIVE means and what light actually did. A brightfield channel is not
    light arriving from a marker; it is the sample lit from behind, an opaque picture of the
    field that everything else sits on top of. Summed into the fluorescence it washes the
    whole scene towards white, and the standard fix a viewer reaches for -- drop it to the
    bottom, blend it NORMAL -- is a thing the bootstrap can simply do.
    """
    normalized = normalize(label)
    if not normalized:
        return False
    if normalized in _TRANSMITTED_EXACT:
        return True
    return any(token in normalized for token in _TRANSMITTED_SUBSTRINGS)


def rgb_components(labels: dict[int, str], channels: int) -> dict[str, int] | None:
    """The red, green and blue indices when the labels say the channels *are* red, green and blue.

    The whole set or nothing, over an axis of exactly three: a photograph has three
    components and a converter that names them names all three. Anything looser reads a
    five-marker acquisition that happens to contain a channel called "Red" as a photograph,
    which is the fusing-three-signals-into-one-layer mistake that got shape-based RGB
    inference deleted -- arrived at from the other direction.

    The indices come from the labels rather than from position, so a converter that wrote the
    components as (blue, green, red) -- which several do -- gets a picture in the right
    colours rather than an inverted one.
    """
    if channels != 3 or len(labels) != 3:
        return None

    components: dict[str, int] = {}
    for index, label in labels.items():
        normalized = normalize(label)
        for component, aliases in _RGB_COMPONENTS.items():
            if normalized in aliases:
                # Two channels claiming one component is not a photograph, whatever else it is.
                if component in components:
                    return None
                components[component] = index

    return components if len(components) == 3 else None
