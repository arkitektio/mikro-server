from pydantic import BaseModel


class FileSignal(BaseModel):
    """A model representing a file event."""

    create: int | None = None
    update: int | None = None
    delete: int | None = None
