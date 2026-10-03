"""rekuest-service: declare a service — the structures it hosts and the signals it emits — to the hub's rekuest.

The source of truth is the ``rekuest-service`` package (``Code/packages/rekuest-service``).
Until it is on PyPI, services carry a copy of this directory, written by that package's
``scripts/vendor.py`` — edit the package, re-vendor, never edit a copy.

A service says what exists, in two separate declarations::

    from rekuest_service import Descriptor, Service, organization_of

    service = Service("mikro", description="Microscopy data")

    # What it HOSTS: a structure, and the descriptors of its objects.
    dataset = service.structure(ArrayDataset, "@mikro/arraydataset",
                                descriptors=[Descriptor("@mikro/n_channels", "INT")], describe=array_descriptors)

    # What it ANNOUNCES: a signal. Here, every save and delete of that model.
    service.model_signal(dataset, organization=organization_of())

and ``*service.urls`` is mounted (``_rekuest/service/manifest``). Rekuest reads the manifest: the
structures and signals become hub-wide catalog entries, which users' triggers are checked
against. A signal carries the provenance token of the task it happened in, and rekuest runs
whatever triggers attach to it. The service keeps no queue and no timer.

A service declares no actions. Offering work to rekuest is what an agent does — for a process
reached over HTTP, a HookAgent of the separate ``rekuest_hook`` package, which may live in a
service's process or anywhere else.

Trust (:mod:`rekuest_service.trust`): no shared secrets. Every instance holds its own Ed25519
key; the hub's coord (lok) vouches for the public halves in a trust bundle. Each request between
rekuest and an instance carries a short-lived JWT signed by the sender, bound to the receiver,
the method, the path and the body.

Settings::

    settings.INSTANCE         PRIVATE_KEY (PKCS#8 PEM), and TRUST_JWKS_URI or an inline TRUST_JWKS
    settings.REKUEST_SERVICE  REKUEST_URL (rekuest on the internal network, e.g.
                              http://rekuest:80/rekuest); optional SERVICE (name override),
                              IDENTIFIER (signing identity override), REKUEST_IDENTIFIER
                              (default live.arkitekt.rekuest), MAX_SKEW (seconds, default 30)
"""

from rekuest_service import trust
from rekuest_service.service import Service, Signal, organization_of
from rekuest_service.structures import Descriptor, Structure

__all__ = ["Descriptor", "Service", "Signal", "Structure", "organization_of", "trust"]
