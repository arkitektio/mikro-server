import strawberry
import strawberry_django
from strawberry.scalars import JSON
from typing import TYPE_CHECKING, Annotated, Optional
from core import filters, order, scalars
from kante.types import Info
import datetime
from enum import Enum
from koherent.models import ProvenanceEntryModel as ProvenanceEntryModel
from koherent.models import Task as TaskModel
import kante

from authentikate import models as amodels

if TYPE_CHECKING:
    from core.types.folder import Folder


@strawberry.type(description="A generic key-value descriptor attached to an object. Clients use descriptors to read arbitrary structured metadata without a dedicated field.")
class Descriptor:
    """A generic key-value descriptor attached to an object. Clients use descriptors to read arbitrary structured metadata without a dedicated field."""

    key: str
    value: scalars.Any
    description: str | None = None


@kante.django_type(amodels.Organization, description="An organization (tenant). Every object in mikro is scoped to exactly one organization, and queries only ever see the current organization's data.")
class Organization:
    """An organization (tenant); every object is scoped to exactly one organization."""

    id: strawberry.ID
    slug: str


@kante.django_type(amodels.User, description="A user account. The sub is the stable subject identifier from the identity provider; creator and assigner fields across the API reference this type.")
class User:
    """A user account; sub is the stable subject identifier from the identity provider."""

    id: strawberry.ID
    sub: str
    preferred_username: str
    active_organization: Organization | None = None


@kante.django_type(amodels.Membership, description="A user's membership in an organization, carrying the roles they hold there.")
class Membership:
    """A user's membership in an organization, carrying the roles they hold there."""

    id: strawberry.ID
    user: User
    organization: Organization
    roles: list[str]
    is_active: bool
    folders: list[Annotated["Folder", strawberry.lazy("core.types.folder")]]


@kante.django_type(amodels.Client, description="An OAuth client (application) that can act against the API, e.g. the app a change was made through.")
class Client:
    """An OAuth client (application) that can act against the API."""

    id: strawberry.ID
    client_id: str
    name: str


@strawberry.enum(description="The type of change that was made.")
class HistoryKind(str, Enum):
    """The type of change that was made."""

    CREATE = "+"
    UPDATE = "~"
    DELETE = "-"


#: What the JSON scalar can encode as-is. Anything else -- a datetime, a Decimal, a UUID, a
#: model instance -- goes through ``str``, since strawberry's JSON scalar serializes through
#: the stdlib encoder.
_JSON_NATIVE = (str, int, float, bool, type(None))


def _to_json_safe(value: object) -> object:
    """Coerce a raw history value to something the JSON scalar can encode, recursively."""
    if isinstance(value, _JSON_NATIVE):
        return value
    if isinstance(value, dict):
        return {str(key): _to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(item) for item in value]
    return str(value)


@strawberry.type(description="A change made to a model.")
class ModelChange:
    """The change made to a model."""

    field: str = strawberry.field(description="The field that was changed.")
    old_value: str | None = strawberry.field(description="The old value of the field, rendered as text.")
    new_value: str | None = strawberry.field(description="The new value of the field, rendered as text.")
    old_value_json: JSON | None = strawberry.field(description="The old value of the field, preserving its native JSON type.")
    new_value_json: JSON | None = strawberry.field(description="The new value of the field, preserving its native JSON type.")


@kante.django_type(
    TaskModel,
    filters=filters.TaskFilter,
    ordering=order.TaskOrder,
    pagination=True,
    description="A validated Rekuest task under which objects were created or changed.",
)
class Task:
    """A validated Rekuest task under which objects were created or changed."""

    id: strawberry.ID
    task_id: str = kante.django_field(description="The rekuest task id")
    parent_task_id: str | None = kante.django_field(description="The immediate parent task id, if any")
    root_task_id: str = kante.django_field(description="The root task id of the whole causal tree")
    assigner: User | None = kante.django_field(description="The root human causer that assigned the task")
    assigner_sub: str = kante.django_field(description="The raw root human causer sub claim")
    caller_sub: str = kante.django_field(description="The immediate causer of this hop")
    agent_sub: str = kante.django_field(description="The executing agent user sub")
    agent_client_id: str = kante.django_field(description="The executing agent OAuth client id")
    issuer: str = kante.django_field(description="The provenance issuer id")
    token_id: str = kante.django_field(description="The unique single-use token id")
    args_hash: str = kante.django_field(description="The SHA-256 of the canonicalized args")
    args_hash_algorithm: str = kante.django_field(description="The args canonicalization algorithm/version")
    organization: Organization = kante.django_field(description="The organization the task ran in")
    created_at: datetime.datetime


@strawberry_django.type(ProvenanceEntryModel, pagination=True, description="A provenance event for a model.")
class ProvenanceEntry:
    """A change made to a model."""

    client: Client | None
    task: Optional[Task] = strawberry_django.field(
        description="The task during which the change occurred, if any."
    )

    @strawberry_django.field(description="User who made the change.")
    def user(self, info: Info) -> User | None:
        """This method returns the user who made the change."""
        return self.history_user  # type: ignore

    @strawberry_django.field(description="The type of change that was made.")
    def kind(self, info: Info) -> HistoryKind:
        """This method returns the type of change that was made."""
        return self.history_type

    @strawberry_django.field(description="The date of the change.")
    def date(self, info: Info) -> datetime.datetime:
        """This method returns the date of the change."""
        return self.history_date

    @strawberry_django.field(description="The ID of the history entry.")
    def id(self, info: Info) -> strawberry.ID:
        """This method returns the ID of the history entry."""
        return self.history_id

    @strawberry_django.field(description="The effective changes made to the model.")
    def effective_changes(self, info: Info) -> list[ModelChange]:
        """This method returns the effective changes made to the model."""
        new_record, old_record = self, self.prev_record

        changes = []
        if old_record is None:
            return changes

        delta = new_record.diff_against(old_record)
        for change in delta.changes:
            # `diff_against` yields the *raw* column values -- a JSONField's list, an FK's
            # model instance, a datetime -- and both text fields are `String`. Handing a
            # list to a String scalar is not a null, it is a GraphQLError that fails the
            # whole query, so one JSON column made every provenance read on that model
            # unanswerable. Stringify for the text pair, keep the native value on the JSON
            # pair, and let None stay null rather than becoming the literal "None".
            changes.append(
                ModelChange(
                    field=change.field,
                    old_value=None if change.old is None else str(change.old),
                    new_value=None if change.new is None else str(change.new),
                    old_value_json=None if change.old is None else _to_json_safe(change.old),
                    new_value_json=None if change.new is None else _to_json_safe(change.new),
                )
            )

        return changes


@strawberry.type(description="A permission a specific user holds on a specific object. Clients use it to inspect and manage per-object access control.")
class UserObjectPermission:
    """A permission a specific user holds on a specific object. Clients use it to inspect and manage per-object access control."""

    user: User = kante.django_field()
    permission: str = kante.django_field()
