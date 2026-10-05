from pydantic import BaseModel


class FileSignal(BaseModel):
    """A model representing a file event."""

    create: int | None = None
    update: int | None = None
    delete: int | None = None


class AnnotationSignal(BaseModel):
    """An annotation event, relayed by id so the subscriber re-fetches through its own org scope.

    ``create_many`` is the bulk draw's one message: ``createAnnotations`` inserts through
    ``bulk_create``, which fires no ``post_save``, and one message per row would cost each
    subscriber one query per shape.

    The ids are strings: an annotation's primary key is a UUID, not the integer a file's is.
    """

    create: str | None = None
    create_many: list[str] | None = None
    update: str | None = None
    delete: str | None = None


class RowSignal(BaseModel):
    """A create/update/delete event for a row with an integer primary key, relayed by id.

    Shared by the layer, dataset and scene channels: what tells them apart is the channel's
    name, not the payload shape. Exactly one field is set per message.
    """

    create: int | None = None
    update: int | None = None
    delete: int | None = None
