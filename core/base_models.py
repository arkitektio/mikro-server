from pydantic import BaseModel, Field


class SliceModel(BaseModel):
    axis: str = Field(..., description="The name of the axis the slice acts on, e.g. 'x', 'y', 'z', 'c', or 't'")
    start: int | None = Field(default=None, description="The starting index of the slice, or None to start from the beginning")
    stop: int | None = Field(default=None, description="The stopping index of the slice, or None to go to the end")
    step: int | None = Field(default=None, description="The step size of the slice, or None to use the default step")


class SliceInputModel(BaseModel):
    axis: str
    start: int | None = None
    stop: int | None = None
    step: int | None = None


class WindowModel(BaseModel):
    """A window along one axis of a container that is not an array: the positions kept, by value.

    Where a slice re-indexes (lens voxel 0 is dataset voxel `start`), a window only keeps:
    a table row whose `t` lies in it is the same row with the same `t`, so a windowed lens
    lives in its container's own coordinate system and owns no edge.
    """

    axis: str = Field(..., description="The name of the axis the window constrains, e.g. 't' for a table's time column")
    min: float | None = Field(default=None, description="The smallest position kept, inclusive, or None for no lower bound")
    max: float | None = Field(default=None, description="The largest position kept, inclusive, or None for no upper bound")


class WindowInputModel(BaseModel):
    axis: str
    min: float | None = None
    max: float | None = None
