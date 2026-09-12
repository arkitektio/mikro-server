"""Typed models for a recorded microscope (Optikit) state.

The hardware truth at the moment of acquisition -- stage pose, environment,
per-device settings -- as composable pydantic models with kanne quantities,
mirrored into GraphQL input and output types the same way the lightpath graph
is. The state is stored on :class:`core.models.OptikitState.state` as the
model's dump and reconstructed on read, so the JSON column never grows a shape
the types cannot express.
"""
