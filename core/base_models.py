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
