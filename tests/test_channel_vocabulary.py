"""What a recorded channel name is allowed to decide.

`core.logic.channels` is a dictionary of fluorophores, and a dictionary is a thing that is
wrong sometimes. These tests pin the two properties that make it safe to keep anyway: an
unrecognized name decides *nothing* (the caller's index cycle survives untouched, which is
what every channel had before), and the one place a name decides something structural --
whether three channels are one photograph -- takes the whole set or none of it.

No database. The vocabulary is a pure function of a string, and testing it through a scene
would hide which half of the answer came from where.
"""

import pytest

from core import enums
from core.logic import channels


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("DAPI", enums.ColorMap.BLUE),
        ("Hoechst 33342", enums.ColorMap.BLUE),
        ("GFP", enums.ColorMap.GREEN),
        ("eGFP", enums.ColorMap.GREEN),
        ("mCherry", enums.ColorMap.RED),
        ("Cy5", enums.ColorMap.MAGENTA),
        ("Alexa Fluor 647", enums.ColorMap.MAGENTA),
        # Punctuation and case are not part of a name.
        ("alexa-fluor-647", enums.ColorMap.MAGENTA),
        # A fluorophore inside a construct name: what a converter writes far more often than
        # the bare name.
        ("H2B-GFP", enums.ColorMap.GREEN),
        # Longest match wins, or "far red" would be read as a bare red.
        ("Far Red", enums.ColorMap.MAGENTA),
        ("Texas Red", enums.ColorMap.RED),
    ],
)
def test_a_recorded_fluorophore_decides_its_hue(label: str, expected: "enums.ColorMap"):
    """The name ingest recorded says what colour the channel is. It always did; nothing read it."""
    assert channels.colormap_for(label) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("640", enums.ColorMap.MAGENTA),
        ("ch_488", enums.ColorMap.GREEN),
        ("405 nm", enums.ColorMap.BLUE),
        ("561", enums.ColorMap.RED),
    ],
)
def test_a_wavelength_in_the_name_decides_the_hue(label: str, expected: "enums.ColorMap"):
    """Half the world names a channel after its laser line, and that is as good as a fluorophore."""
    assert channels.colormap_for(label) == expected


@pytest.mark.parametrize("label", ["channel 2", "ch1", "", None, "series 4", "Hoechst"])
def test_an_uninformative_name_decides_nothing_except_when_it_does(label):
    """None is the answer that keeps the index cycle, and it has to be reachable.

    A dictionary that returns *something* for every string would quietly replace the
    distinguishable-hue cycle with whatever it happened to match. `Hoechst` is in the list to
    show the boundary: it is a real name and does decide, and a five-digit catalogue number
    next to it (tested above) must not be read as a wavelength.
    """
    assert channels.colormap_for(label) in (None, enums.ColorMap.BLUE)
    if label == "Hoechst":
        assert channels.colormap_for(label) == enums.ColorMap.BLUE
    else:
        assert channels.colormap_for(label) is None


@pytest.mark.parametrize("label", ["Brightfield", "bright field", "BF", "DIC", "Phase contrast", "TL", "label-free"])
def test_transmitted_light_is_told_apart(label: str):
    """Not for the colour: for the blend. Brightfield summed into fluorescence washes a scene out."""
    assert channels.is_transmitted(label) is True


@pytest.mark.parametrize("label", ["GFP", "DAPI", "channel 0", "", None, "TRITC", "BFP"])
def test_a_fluorophore_is_not_transmitted_light(label):
    """The two-letter aliases are the risk here: `bf` and `tl` must not fire inside a real name.

    `BFP` is the case that would break a substring match -- it contains `bf`, and it is a blue
    fluorescent protein rather than a brightfield channel.
    """
    assert channels.is_transmitted(label) is False


def test_three_channels_named_red_green_blue_are_a_photograph():
    """The whole set, and the indices come from the labels rather than from position."""
    assert channels.rgb_components({0: "Red", 1: "Green", 2: "Blue"}, 3) == {"red": 0, "green": 1, "blue": 2}


def test_the_components_are_read_in_the_order_the_converter_wrote_them():
    """Several converters write (blue, green, red). Assuming 0, 1, 2 inverts the picture."""
    assert channels.rgb_components({0: "blue", 1: "green", 2: "red"}, 3) == {"red": 2, "green": 1, "blue": 0}


@pytest.mark.parametrize(
    ("labels", "channel_count"),
    [
        # A fluorescence panel that happens to contain a channel called "Red". Reading this as
        # a photograph would fuse five signals into one layer, which is the mistake that got
        # shape-based RGB inference deleted, arrived at from the other direction.
        ({0: "Red", 1: "Green", 2: "Blue", 3: "DAPI", 4: "Cy5"}, 5),
        # Two thirds of a set is not a set.
        ({0: "Red", 1: "Green"}, 3),
        # Named, but not as components.
        ({0: "DAPI", 1: "GFP", 2: "mCherry"}, 3),
        # Nothing recorded at all: the case that must stay a layer per channel forever.
        ({}, 3),
        # Two channels claiming one component is not a photograph, whatever else it is.
        ({0: "red", 1: "red", 2: "blue"}, 3),
    ],
)
def test_anything_less_than_the_whole_set_is_not_a_photograph(labels: dict, channel_count: int):
    """All three, over an axis of exactly three, or none of it."""
    assert channels.rgb_components(labels, channel_count) is None
