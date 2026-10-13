"""The service layer: domain orchestration shared by mutations and signals.

Modules here own the "how" of creating and transforming domain objects
(datasets, lenses, annotations); ``core.mutations`` resolvers stay thin — argument
resolution and a call into this package. Logic modules may import
``core.models``, ``core.inputs``, ``core.scoping`` and ``core.creation``,
but never ``core.mutations`` or ``core.types``.
"""
