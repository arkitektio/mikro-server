"""rekuest-hook: a HookAgent — an agent the hub's rekuest reaches over HTTP — and its actions.

The source of truth is the ``rekuest-hook`` package (``Code/packages/rekuest-hook``). Until it is
on PyPI, processes that run an agent carry a copy of this directory, written by that package's
``scripts/vendor.py`` — edit the package, re-vendor, never edit a copy.

    from rekuest_hook import HookAgent

    agent = HookAgent("mikro", description="mikro's housekeeping")

    @agent.action
    def reembed_stale(organization: str) -> dict:
        '''Re-embed stale rows.'''
        return {"reembedded": ...}

and ``*agent.urls`` is mounted (``_rekuest/hook`` and its manifest). Rekuest reads the manifest
and gives every organization the agent: its actions are real actions. Nothing is wired for
anyone: when an action runs (a schedule, a trigger, by hand) is the organization's own
automation. A run arrives as a signed Assign; the agent reports Started at once, runs the
function in a thread, and reports its return value (or the error) back to rekuest's intake. The
process keeps no queue and no timer.

An agent is not a service. A service (``rekuest_service.Service``) says what exists: structures
and signals. An agent says what can be done. Neither knows the other; a process may be one, the
other or both. The only thing this package takes from ``rekuest_service`` is its ``trust``
module — the instance key every process of a hub signs with, which is neither's.

Settings::

    settings.INSTANCE      PRIVATE_KEY (PKCS#8 PEM), and TRUST_JWKS_URI or an inline TRUST_JWKS
    settings.REKUEST_HOOK  REKUEST_URL (rekuest on the internal network, e.g.
                           http://rekuest:80/rekuest); optional AGENT (name override),
                           IDENTIFIER (signing identity override), REKUEST_IDENTIFIER
                           (default live.arkitekt.rekuest), MAX_SKEW (seconds, default 30)
"""

from rekuest_hook.agent import Action, HookAgent

__all__ = ["Action", "HookAgent"]
