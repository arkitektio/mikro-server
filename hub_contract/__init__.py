"""What a service image answers a hub's installer.

A hub is a set of service images run together by an installer. The installer knows the hub —
where the database is, which other services run, which keys they trust — and should know
nothing about a service beyond what the service's own image tells it. This package is how an
image tells it: one entry point (``python -m hub_contract <verb>``), the same in every image.

==============  ================================================================
``describe``    what the service needs from a hub and offers to it
``render``      this release's config, written from the hub's facts
``check``       whether this release reads a config as written
``migrate``     the release's database migrations, as a step with an answer
``upgrade``     what the release does to its data between two versions
==============  ================================================================

A service declares itself once, in a module named by ``HUB_CONTRACT`` (set in its image)::

    contract = Contract(description=..., settings=Settings, render=render)

See :mod:`hub_contract.facts` for what a hub says, :mod:`hub_contract.description` for what a
service says, and :mod:`hub_contract.cli` for the verbs and their exit codes.
"""

from hub_contract import blocks
from hub_contract.contract import Contract, Refused
from hub_contract.description import Description, Needs, Offers, Scope
from hub_contract.facts import Facts, Peer
from hub_contract.json_types import JSON

__all__ = ["JSON", "Contract", "Description", "Facts", "Needs", "Offers", "Peer", "Refused", "Scope", "blocks"]
