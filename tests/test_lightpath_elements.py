"""Every element kind is buildable, storable and readable -- checked, not assumed.

`ElementKind` had seventeen members and the storage union had thirteen. The four missing
ones were not merely unsupported: the enum is published in the SDL, so the schema
*advertised* SHUTTER, POLARIZER, WAVEPLATE and APERTURE as things a client could send. And
ingest never built the storage model at all -- it dumped the input model straight into the
JSON column -- so such an element was accepted, written, and blew up much later, on a
*query*, as a raw pydantic union-tag report:

    Input tag 'SHUTTER' found using 'kind' does not match any of the expected tags: ...

Two properties keep that from recurring, and both are asserted here rather than reviewed:
the four lists of element kinds (the enum, the storage table, the input members, the
registered read types) are **each total over `ElementKind`**, and ingest **builds the
storage model**, so the column cannot hold a shape the read side cannot rebuild.
"""

import re

import pytest

from lightpath.constants import interface_types
from lightpath.enums import ElementKind
from lightpath.inputs.models import ELEMENT_MEMBERS, LightpathGraphInputModel
from lightpath.inputs.types import OpticalElementInput, element_union_types
from lightpath.objects.models import ELEMENT_MODEL_BY_KIND, LightpathGraphModel
from mikro_server.schema import schema


def test_every_element_kind_has_a_storage_model() -> None:
    """The enum is the SDL's promise; the table is what can keep it."""
    assert set(ELEMENT_MODEL_BY_KIND) == set(ElementKind), "an ElementKind with no storage model is advertised in the SDL and unbuildable"


def test_every_element_kind_has_an_input_member() -> None:
    """A kind a client cannot send is as broken as one the server cannot store."""
    assert {member for member in ELEMENT_MEMBERS} == {kind.value for kind in ElementKind}


def test_every_element_kind_has_a_registered_read_type() -> None:
    """An unregistered `OpticalElement` subtype vanishes from the SDL silently.

    It is referenced by no field -- the interface is what a query selects -- so dropping
    one from `types=[...]` is invisible until a graph containing that kind is read.
    """
    assert len(interface_types) == len(list(ElementKind))
    assert len({t.__name__ for t in interface_types}) == len(interface_types), "no duplicates"


@pytest.mark.parametrize("kind", list(ElementKind))
def test_each_kind_round_trips_from_input_to_storage_and_back(kind: ElementKind) -> None:
    """Build the minimal element of every kind, store it, and read it back.

    Parametrized over the enum rather than over a hand-written list, so a new kind is
    covered the moment it is declared.
    """
    element: dict = {"label": f"a {kind.value}", "kind": kind.value, "ports": []}
    if kind is ElementKind.LASER:
        element["nominal_wavelength"] = "488 nm"  # its one required field

    graph = LightpathGraphInputModel(elements=[element], edges=[])
    stored = graph.to_graph().model_dump(mode="json")

    rebuilt = LightpathGraphModel(**stored)
    assert rebuilt.elements[0].kind == kind
    assert isinstance(rebuilt.elements[0], ELEMENT_MODEL_BY_KIND[kind])


def test_a_field_that_contradicts_the_kind_is_an_error_not_a_passenger() -> None:
    """The union's whole point: `kind` selects a member, and strays are named.

    Before this the input was one fat model that read `kind` and checked nothing against
    it, so a `numericalAperture` on a shutter was carried into the stored JSON and read
    back by nothing.

    Asserted on the *wire* type, which is where a client's element arrives and where
    `to_pydantic` routes it through `parse_union_member` -- the same seam the transform
    union uses, and the reason the message is a sentence rather than a pydantic report.
    """
    element = OpticalElementInput(label="AOTF", kind=ElementKind.SHUTTER, numerical_aperture=0.8)

    with pytest.raises(ValueError, match="does not read `numericalAperture`") as raised:
        element.to_pydantic()
    assert "A SHUTTER optical element" in str(raised.value), "the message names the kind as well as the field"


def test_the_reported_payload_is_accepted_and_reads_back() -> None:
    """The exact shape from the bug report: an AOTF gate, sent as a SHUTTER with a gain."""
    graph = LightpathGraphInputModel(
        elements=[{"id": "aotf-640", "label": "AOTF 640", "kind": "SHUTTER", "ports": [], "gain": 1.5, "shutter_type": "AOTF"}],
        edges=[],
    )
    rebuilt = LightpathGraphModel(**graph.to_graph().model_dump(mode="json"))

    element = rebuilt.elements[0]
    assert element.id == "aotf-640" and element.gain == 1.5 and element.shutter_type == "AOTF"


def test_the_sdl_declares_every_element_type_and_its_union_member() -> None:
    """The registration half, pinned where it actually fails: in the SDL.

    A concrete `OpticalElement` subtype is referenced by no field, and so is a
    `@unionElementOf` member input -- both live in the schema's `types=[...]` alone, and
    dropping one erases it from the SDL with nothing to notice. That is the same shape of
    silence the missing storage models had: nothing complains until a client sends the kind.
    """
    sdl = schema.as_str()

    for type_ in interface_types:
        assert f"type {type_.__name__} implements OpticalElement" in sdl, f"{type_.__name__} missing from the SDL"

    for member in element_union_types:
        start = sdl.find(f"input {member.__name__} ")
        assert start >= 0, f"{member.__name__} missing from the SDL"
        header = sdl[start : sdl.find("{", start)]
        assert '@unionElementOf(union: "OpticalElementInput", discriminator: "kind", key: ' in header, f"{member.__name__} lacks its annotation"


def test_every_member_declares_the_parents_common_fields() -> None:
    """A member is a usable input type, not a fragment of one.

    GraphQL input types have no inheritance, so a client generating a member from its
    `@unionElementOf` annotation gets exactly the fields the member declares. Omitting the
    common ones -- which is how these were first written -- leaves it unable to say what
    the element is called, where it sits, or what ports it has, and the generated type is
    unusable without hand-patching. `kind` is included: it carries the member's own value
    as its default, so the type says which member it is.
    """
    sdl = schema.as_str()
    common = ["id", "label", "kind", "pose", "ports", "manufacturer", "model", "serialNumber"]

    for member in element_union_types:
        start = sdl.find(f"input {member.__name__} ")
        body = sdl[start : sdl.find("\n}", start)]
        missing = [field for field in common if not re.search(rf"^\s*{field}:", body, re.M)]
        assert not missing, f"{member.__name__} does not declare the common fields {missing}"

    # And the discriminator defaults to the member's own key, which is what lets a client
    # construct one without threading `kind` in separately.
    shutter = sdl[sdl.find("input ShutterElementInput") : sdl.find("\n}", sdl.find("input ShutterElementInput"))]
    assert "kind: ElementKind! = SHUTTER" in shutter

    # And the kinds a client may name are exactly the kinds something can build.
    enum_body = sdl[sdl.find("enum ElementKind") : sdl.find("}", sdl.find("enum ElementKind"))]
    for kind in ElementKind:
        assert kind.value in enum_body
