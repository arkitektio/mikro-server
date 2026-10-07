"""mikro's hook agent: the work the hub's rekuest can ask of this process (``arkitekt_service.hook``).

An agent of its own, not a part of the service declared in ``mikro_server.service``: the service
says what exists, the agent says what can be done. Each has its own entry in the hub's
configuration (``rekuest.services`` / ``rekuest.hook_agents``) and its own endpoints (``urls.py``).

Every organization has the agent, so an action is handed the slug of the organization a run is
for and does that organization's share of the work, nothing else. Nothing is wired: whether and
when an action runs (a schedule, a trigger, by hand) is the organization's own automation.
Nothing here loops or waits: each run is one pass rekuest started.
"""

from arkitekt_service.hook import HookAgent

agent = HookAgent("mikro", description="mikro's housekeeping: work on its own data.")
