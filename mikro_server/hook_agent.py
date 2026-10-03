"""mikro's hook agent: the work the hub's rekuest can ask of this process (vendored ``rekuest_hook``).

An agent of its own, not a part of the service declared in ``mikro_server.service``: the service
says what exists, the agent says what can be done. Each has its own entry in the hub's
configuration (``rekuest.services`` / ``rekuest.hook_agents``) and its own endpoints (``urls.py``).

Every organization has the agent, so an action is handed the slug of the organization a run is
for and does that organization's share of the work, nothing else. Nothing is wired: whether and
when an action runs (a schedule, a trigger, by hand) is the organization's own automation.
Nothing here loops or waits: each run is one pass rekuest started.
"""

from core import models
from embeddings.healer import reembed_all
from rekuest_hook import HookAgent

agent = HookAgent("mikro", description="mikro's housekeeping: work on its own data.")

# The models whose name + description are embedded (see ``embeddings.healer``).
_EMBEDDED_MODELS = (models.Folder, models.ArrayDataset, models.TableDataset)


@agent.action(
    interface="reembed_stale",
    name="Re-embed stale rows",
    description="Re-embed every row of the organization whose vector was produced by another embedding model, or by none.",
)
def reembed_stale(organization: str) -> dict:
    """One pass over the organization's embedded rows, in row-locked batches (N replicas may run it at once)."""
    return {"reembedded": reembed_all(_EMBEDDED_MODELS, max_batches=50, organization=organization)}
