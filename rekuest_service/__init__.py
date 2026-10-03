"""rekuest-service: declare a service (its structures and signals) and its HookAgent (its actions) to the hub's rekuest.

The source of truth is the ``rekuest-service`` package (``Code/packages/rekuest-service``).
Until it is on PyPI, services carry a copy of this directory, written by that package's
``scripts/vendor.py`` — edit the package, re-vendor, never edit a copy.

Two things are declared, and they are different things::

    from rekuest_service import Descriptor, HookAgent, Service, organization_of

    # The SERVICE: what exists. Structures it hosts (with their descriptors) and signals it emits.
    service = Service("mikro", description="Microscopy data")
    service.structure(ArrayDataset, "@mikro/arraydataset", organization=organization_of(),
                      descriptors=[Descriptor("@mikro/n_channels", "INT")], describe=array_descriptors)

    # Its HOOKAGENT: what can be done. An agent that runs inside the service and offers actions.
    agent = HookAgent(service)

    @agent.action(default_interval=300)
    def reembed_stale() -> dict:
        '''Re-embed stale rows.'''
        return {"reembedded": ...}

and ``*service.urls`` is mounted (``_rekuest/hook`` and its manifest). Rekuest reads the manifest:
the service's structures and signals become hub-wide catalog entries, which users' triggers are
checked against; the agent is provisioned as a HookAgent whose actions are real actions, each
scheduled when it declares a default. A run arrives as a signed Assign; the agent reports Started
at once, runs the function in a thread, and reports its return value (or the error) back to
rekuest's intake. Every save and delete of a hosted model is announced as a signal — with the
provenance token of the task it happened in — and rekuest runs whatever triggers attach to it.
The service keeps no queue and no timer.

Trust (:mod:`rekuest_service.trust`): no shared secrets. Every instance holds its own Ed25519
key; the hub's coord (lok) vouches for the public halves in a trust bundle. Each request between
rekuest and the service carries a short-lived JWT signed by the sender, bound to the receiver,
the method, the path and the body.

Settings::

    settings.INSTANCE      PRIVATE_KEY (PKCS#8 PEM), and TRUST_JWKS_URI or an inline TRUST_JWKS
    settings.REKUEST_HOOK  REKUEST_URL (rekuest on the internal network, e.g.
                           http://rekuest:80/rekuest); optional SERVICE (name override),
                           IDENTIFIER (signing identity override), REKUEST_IDENTIFIER
                           (default live.arkitekt.rekuest), MAX_SKEW (seconds, default 30)

The module-level ``action`` / ``declare_signal`` / ``emit`` / ``views.urlpatterns`` register on
a process-default service; they remain for existing callers.
"""

from rekuest_service import trust
from rekuest_service.agent import HookAgent
from rekuest_service.registry import action, declare_signal, declared_signals, registered
from rekuest_service.service import Service, Signal, organization_of
from rekuest_service.signals import emit
from rekuest_service.structures import Descriptor, Structure

__all__ = ["Descriptor", "HookAgent", "Service", "Signal", "Structure", "action", "declare_signal", "declared_signals", "emit", "organization_of", "registered", "trust"]
