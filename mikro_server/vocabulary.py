"""The descriptors of mikro's arrays, as data: the server-side twin of the client's vocabulary.

Declared once, on the structures in ``mikro_server.contract`` (``hosts``), and read from there
by everything that states them: a signal carries the object's descriptors so rekuest can match
it against triggers and against the ``requires`` of the ports it would be fed to, and the
GraphQL types answer them as ``descriptors``, so a client can ask which actions take the object
in hand. Those ports were declared by the mikro client's spec vocabulary
(``mikro.arkitekt.specs``), so these keys and their meaning must match
``mikro.arkitekt.specs.lens_descriptors`` exactly: one count per axis type (zero included —
``Still`` and ``SingleChannel`` match on absence) plus the total extent along the channel and
time axes.

Nothing here imports Django: the contract says these before the service has a config, and
``core.descriptors`` computes them from the same keys.
"""

from arkitekt_service.contract import Descriptor

KEY_BY_AXIS_TYPE = {
    "SPACE": "@mikro/n_space_axes",
    "TIME": "@mikro/n_time_axes",
    "CHANNEL": "@mikro/n_channel_axes",
    "SPECTRUM": "@mikro/n_spectrum_axes",
    "MICROTIME": "@mikro/n_microtime_axes",
}
EXTENT_KEYS = {"@mikro/n_channels": "CHANNEL", "@mikro/n_timepoints": "TIME"}
#: Every descriptor ``core.descriptors.array_descriptors`` produces — what mikro declares an
#: array dataset and a lens carry.
ARRAY_DESCRIPTORS = [
    *(Descriptor(key=key, type="INT", description=f"How many of its axes are {axis_type} axes") for axis_type, key in KEY_BY_AXIS_TYPE.items()),
    *(Descriptor(key=key, type="INT", description=f"Its total extent along its {axis_type} axes") for key, axis_type in EXTENT_KEYS.items()),
]
#: Descriptors an array carries that nothing computes: provenance, stated by whoever made the
#: data (a ``Provides`` on the action that returned it). Declared so that a port may constrain on
#: them at all -- rekuest refuses a key no service declares -- and absent from every
#: ``describe``, so no object is ever matched or refused on them.
PROVENANCE_DESCRIPTORS = [
    Descriptor(key="@mikro/value_kind", type="STRING", description="What its values mean (e.g. categorical for labels); stated by its producer, never computed"),
]
