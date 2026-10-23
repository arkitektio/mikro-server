"""Provenance and ownership context for object creation.

The write-side counterpart to :mod:`core.scoping`: reads are tenant-scoped
through ``for_org``/``get_for_org``; writes stamp ownership (creator,
organization, membership) and task provenance (created_through,
created_through_by) from a single :class:`CreationContext` built once per
mutation instead of repeating ``info.context.request.*`` and
``get_or_create_task()`` plumbing at every call site.

Ownership kwargs stay explicit at each ``objects.create`` call because models
differ in which fields they carry (e.g. ``Era`` has an organization but no
creator/membership). Only the always-identical ``created_through`` pair is
bundled in :meth:`CreationContext.provenance_kwargs`.
"""

from dataclasses import dataclass
from typing import Any

from kante.context import Client, Membership, Organization, User
from kante.types import Info
from koherent.models import Task
from koherent.utils import get_or_create_task


@dataclass(frozen=True)
class CreationContext:
    """The request identity and task provenance every created object is stamped with."""

    user: User
    organization: Organization
    membership: Membership
    task: Task | None
    #: The OAuth client the request was made through, kept only so :attr:`app_identifier`
    #: can be read when something needs it -- most mutations never do.
    client: Client | None = None

    @classmethod
    def from_info(cls, info: Info) -> "CreationContext":
        """Build the context from a resolver's ``Info`` (sync resolvers only)."""
        request = info.context.request
        return cls(
            user=request.user,
            organization=request.organization,
            membership=request.membership,
            task=get_or_create_task(),
            client=getattr(request, "_client", None),
        )

    @property
    def created_through_by_id(self) -> int | None:
        """The creating task's assigner id, denormalized onto created objects."""
        return self.task.assigner_id if self.task else None

    @property
    def app_identifier(self) -> str | None:
        """The identifier of the app the request was made through, or None for a token naming none.

        The app, never the client: a client is re-minted on every fresh login, an app is
        not, so anything keyed by the caller's identity keys by this. A token without
        ``client_app``/``client_release`` claims binds a client with no release -- "no app",
        not an error, until something needs one.
        """
        return app_identifier_of(self.client)

    def provenance_kwargs(self) -> dict[str, Any]:
        """The ``created_through`` pair taken by every provenance-tracked model."""
        return dict(
            created_through=self.task,
            created_through_by_id=self.created_through_by_id,
        )


def app_identifier_of(client: Client | None) -> str | None:
    """The identifier of the app a client belongs to, or None when it names no app.

    The reading side has no :class:`CreationContext` (building one opens a provenance
    task), so this is the one place the client -> release -> app walk is written.
    """
    release = getattr(client, "release", None)
    app = getattr(release, "app", None)
    return getattr(app, "identifier", None)
