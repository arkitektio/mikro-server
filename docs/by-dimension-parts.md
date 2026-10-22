# BY_DIMENSION parts: one kind per axis subset, FIELD among them

A BY_DIMENSION is **parts side by side**, as OME-NGFF's `byDimension` is: each part maps its own
input axes onto its own output axes by one kind, and the parts are disjoint on both sides, so
every axis has one map.

```graphql
mutation {
  createTransformation(input: {
    input: "<movie's intrinsic system>"     # (t, y, x)
    output: "<world>"                       # (t s, y µm, x µm)
    transform: {
      kind: BY_DIMENSION
      parts: [
        {kind: SCALE, inputAxes: ["y", "x"], outputAxes: ["y", "x"], scale: [0.5, 0.5]}
        {kind: FIELD, inputAxes: ["t"], outputAxes: ["t"],
         field: {system: "<times dataset's system>", unit: "second", min: 0.0, max: 7.5}}
      ]
    }
  }) { id }
}
```

The Python client writes this as `world.register(movie, scale={"y": 0.5, "x": 0.5},
field={"t": times})`, reading the array once to check it.

A part is one of `PartKind`, and reads only its own field beside its axes:

| kind | reads | map |
|---|---|---|
| IDENTITY | -- | the same positions, said twice, converted between the axes' units |
| SCALE | `scale` | one factor per input axis of the part |
| TRANSLATION | `translation` | one offset per input axis of the part |
| AFFINE | `affine` | one row per output axis, one column per input axis plus the offset |
| ROTATION | `affine` | an orthonormal AFFINE |
| FIELD | `field` | the values of an array |

## Nothing passes through implicitly

An axis no part names is one the edge **says nothing about**: it gets no row in `asAffine`, and a
walk does not carry it across. A pass-through is stated, as an IDENTITY part. That is how a
registration crosses a rank boundary -- a `(c, y, x)` dataset placed into a `(t, z, y, x)` world
names `y` and `x` and claims nothing about `c`, `t` or `z` -- and it is why a timelapse mask keying a
`(t, i)` table states `t` as an IDENTITY part beside the FIELD part giving `i`.

## FIELD parts: placing, or keying

A FIELD part's map is an array: the times of an event-triggered movie's frames, a warp field, a
label mask whose pixels are object ids. The array is a node of the graph, not a payload, and its
own value axis says what its numbers are -- none means SCALAR (one value: a time, an id), COORDINATE
means positions, DISPLACEMENT means offsets from the position they are read at.

What the values land on decides what the part does, and it is materialized when the edge is
written (`params.dereference`):

- values landing on an **INDEX** axis are ids: an INDEX axis has no metric, so an id is never a
  position. The part is a **key** -- a label mask keying its object table -- and no walk places
  anything through it. `attributePlans` reads keys; `pathToWorld` never crosses one, and neither
  does the fact walk (which skips every FIELD part: its paths are composed by clients that do not
  evaluate fields).
- everything else **places**: a times array, uneven z planes, a warp, a coordinate grid.

`params.spatial` says whether a SPACE axis is given, which makes the edge DIFFEOMORPHIC: no matrix
spaces voxels unevenly or bends a straight line.

A separate array is linked through `TransformationField`, PROTECTed, so it cannot be deleted from
under the edge reading it; any number of FIELD parts may sit on one edge. A self field -- a mask,
whose own pixels are the map -- is stored as `"self"` and links nothing: it is a fact about the
mask, which the input's CASCADE removes with it.

## What is checked, and what is declared

The server **never reads array contents**, so it checks what the rows can say and records the
rest as declared:

- the field is something standing in yields a value (an array, a mesh or network collection);
- one value axis at most; a scalar field gives exactly one axis;
- an array field is indexed by one axis per consumed axis, one value per position where the
  input has data;
- `unit` converts to every output axis' unit;
- `max > min`. A field read along one axis (a **lookup**) must declare both, and that its values
  *strictly increase* is the writer's claim; the client checks it before sending.

**The declared range is recorded, not used (as of 11.0.0).** `min`/`max` are validated and read
back on `FieldPart`, and nothing else consumes them: extents (`SpaceGraph`) leave an axis a field
gives **unbounded**, and the orkestrator never reads them either -- it fetches a lookup's array
once and takes the slider's range from the first and last value. Bounding a lookup axis by
`[min, max]` is the obvious first consumer; a warp's one pair cannot bound two or three
components, so it would stay unbounded.

## Absolute values, and the epoch as their reference

A field's values are **absolute coordinates on the output axis** (or offsets, for DISPLACEMENT):
`12.3` places that frame at `t = 12.3 s` in the target space, whatever the source's own frames
count from. What turns those coordinates into wall-clock time is the target space's `epoch`, set
explicitly when the space is created (`createCoordinateSystem(epoch:)`, `createScene(epoch:)`):
`wall_clock = epoch + t * unit`. It is a reference of the space, not of the data, and is never
derived from anything else.

## What a placement says

`Layer.asAffine` gives the axes affine parts state in `matrix`/`outputAxes` and the axes FIELD
parts give in `fields`, one entry per destination axis:

```
world(x) = scale * field[inner · x](component) + base · x
```

`inner` has one row per axis the array is indexed by, over the layer's source axes plus a
constant, so it folds in whatever lies between the layer and the edge (a lens slicing `t` from
frame 2, a pyramid stride). `scale` and `base` fold in what lies after it (a session clock sitting
10 s into an experiment) and the unit conversion; `base` also adds the position back for a
DISPLACEMENT field. `component` is the value-axis position of a vector field. A field read along
one source axis -- a lookup -- also states `inputAxis`, `innerScale` and `innerOffset`:

```
world = scale * values[innerScale * i + innerOffset] + offset
```

`total` counts the field's axes as covered. The values are positions of sample **centres**, and
the map is piecewise-linear between them, so a fractional index (a pyramid level's) is still a
position. A FIELD part is walked forwards only: its inverse is a search through values the server
does not read.

## Composed is not drawn

Every placing FIELD part composes, so a layer over a warped volume is accepted, placed, and has an
`asAffine`. Whether a viewer can *draw* it is the viewer's question: the orkestrator draws a
lookup on a non-rendered axis (a time slider snapping to the instants, showing the last frame at
or before it) and shows anything else -- a field on a rendered axis, a warp -- as "placed through
a field, not drawable yet".

## Invariance

The weakest of the parts'. An AFFINE whose linear block is diagonal reads like the scale it is (a
pixel size beside a stage position stays SIMILARITY when the factors agree), the same equality
check a SCALE's vector gets and no SVD.

Pinned by `tests/test_by_dimension_parts.py`. Keying a table off a mask, step by step:
[`field-transforms-api.md`](field-transforms-api.md).
