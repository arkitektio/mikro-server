# Axis lookups: one axis read from an array, the rest still affine

An event-triggered movie's frames were taken at uneven instants (`0, 0.1, 0.15, 2.0, 7.5 s`),
so no scale and offset along `t` states when frame 3 was. A **lookup** says it: one axis of a
`BY_DIMENSION` edge whose output is the value of a 1-D array at the input index, while the axes
the edge names keep their scale, translation or affine.

```graphql
mutation {
  createTransformation(input: {
    input: "<movie's intrinsic system>"     # (t, y, x)
    output: "<world>"                       # (t s, y µm, x µm)
    transform: {
      kind: BY_DIMENSION
      inputAxes: ["y", "x"]                 # the axes the PARAMETERS act on
      outputAxes: ["y", "x"]
      scale: [0.5, 0.5]
      lookup: {
        axis: "t"                           # read along this input axis…
        field: "<times dataset's system>"   # …from this 1-D array, alone in its system
        unit: "second"                      # the values' unit; converted into the world's
        min: 0.0                            # first and last value, DECLARED
        max: 7.5
      }
    }
  }) { id }
}
```

The Python client says the same thing as `world.register(movie, scale={"y": 0.5, "x": 0.5},
field={"t": times})`, reading the array once to check it.

## What is checked, and what is declared

The server **never reads array contents**, so it checks what the rows can say and records the
rest as declared:

- the field system holds exactly one dataset, rank one, with one value per position of `axis`;
- `unit` converts to the output axis' unit;
- `axis` is not also one of the named axes (an axis has one map);
- `max > min`. That the values *strictly increase* in between is the writer's claim; the client
  checks it before sending.

One lookup per edge: its array rides the edge's `field` column, PROTECTed, so deleting the times
dataset is refused while the edge stands. A second uneven axis registers through an intermediate
space.

## Absolute values, and the epoch as their reference

A lookup's values are **absolute coordinates on the output axis**: `12.3` places that frame at
`t = 12.3 s` in the target space, whatever the source's own frames count from. There is no offset to
state -- the array states every frame's place itself. What turns those coordinates into wall-clock
time is the target space's `epoch`, set explicitly when the space is created
(`createCoordinateSystem(epoch:)`, `createScene(epoch:)`): `wall_clock = epoch + t * unit`. It is a
reference of the space, not of the data, and is never derived from anything else (an acquisition
window included). The movie's own pixel grid needs no epoch.

## What a placement says

`Layer.asAffine` keeps the affine axes in `matrix`/`outputAxes` and gives the looked-up one in
`lookups`:

```
world = scale * values[innerScale * i + innerOffset] + offset
```

`innerScale`/`innerOffset` fold in whatever lies between the layer and the edge (a lens slicing
`t` from frame 2 gives `innerOffset: 2`); `scale`/`offset` fold in what lies after it (a session
clock sitting 10 s into an experiment gives `offset: 10`) and the unit conversion. `total` counts
looked-up axes as covered.

The values are positions of sample **centres**, and the map is piecewise-linear between them, so
a fractional index (a pyramid level's `f * i + (f - 1) / 2`) is still a position. Which frame a
viewer *shows* at a world time is a display choice on top: the orkestrator shows the last frame at
or before it.

## Geometry

A lookup along a non-SPACE axis (time, an index) leaves the edge AFFINE: space is untouched, the
edge is condensable, and a layer over it is drawn. A lookup along a SPACE axis (uneven z planes)
makes it DIFFEOMORPHIC — no matrix can space voxels unevenly — so the placement gate refuses a
layer over it. A lookup edge is walked forwards only: its inverse is a search through values the
server does not read.

Pinned by `tests/test_axis_lookups.py`.
