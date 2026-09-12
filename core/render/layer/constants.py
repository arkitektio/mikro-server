"""Concrete implementations of the ``LayerRenderNode`` interface.

Interface implementations that are only reachable through the interface (here,
via ``BlendNode.children``) are not auto-discovered by strawberry; they must be
registered in the schema's ``types=`` list or they are dropped from the SDL.
"""

from core.render.layer import types


layer_render_node_types = [
    types.ChannelSourceNode,
    types.BlendNode,
    types.ProjectionNode,
    types.PhasorNode,
]
