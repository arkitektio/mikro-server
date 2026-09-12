"""kanne — Pint-backed GraphQL quantity scalars with nano-base integer storage.

Physical quantities cross the wire as Pint quantity strings (e.g. ``"5 ms"``,
``"0.065 V"``) and are normalized internally to an integer count of the
quantity's nano-base unit (nanoseconds, nanovolts, ...). A single global,
configurable :class:`pint.UnitRegistry` backs all parsing and validation.
"""
