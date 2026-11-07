# chart

mikro composes data into a **scene** and a **chart**, and one question tells them apart: *what
decides where a thing is drawn on screen?*

- A **scene** shows *where*. Every screen axis is an axis of its world, a place; each layer is
  placed there by the graph's edges; values are drawn as appearance (an image's intensity as
  colour, a table's `area` as the colour of its dots).
- A **chart** shows *how much*. Values are drawn as position, and **at most one axis comes from
  the graph**. An AXIS chart lays its layers along its world's one measured axis (intensity
  against `t`); a VALUES chart's world is whose rows -- a table's space, not drawn -- and it
  draws those rows at the values of the columns it names (`area` against `volume`).

If rotating the view makes sense, it is a scene; if rotating would mix units, it is a chart. The
same lens draws both ways -- nuclei at `(y, x)` on the image, and at `(area, volume)` in a chart
VALUES chart. Grids, diagrams and boards, the compositions after these two, are planned in
`docs/rfc12-compositions.md`.

A chart is built on the same coordinate graph as a scene and is its equal, not its child.
It lives in its own Django app so that the difference between "the data layer" and "one way
of looking at it" is a directory boundary.

## Concepts

**Chart.** A name, a `kind`, and a `world`: the domain its layers' rows live in. The chart adopts
the world and never owns it: several charts can share one, the space outlives all of them, and
deleting a chart deletes no space.
- An **AXIS** chart's world is a `CoordinateSystem` with exactly one axis, measured
  (`METRIC_AXIS_TYPES`) and carrying a unit, which it draws.
- A **VALUES** chart's world is the space whose rows it draws: a table's -- usually its INDEX
  axis (`cell_id`), a localization table's frame. It is not drawn; every layer's rows are placed
  into it, so two layers in one world are about the same rows (a table and the PCA table
  derived from it, row for row). Its drawn axes are the units of the columns its layers bind.

A chart is one kind or the other, never both: a position along a world axis and a measurement
are not one kind of thing, even in one unit.

**ChartLayer.** One reading of data, drawn in a chart. It names its source lens and carries view
state: name, order, visibility, opacity, colour, and how it is drawn. It owns no data and stores
nothing about where the data sits; a value layer stores which columns it reads.

**Kind.** What a layer reads.

| Kind | Chart | Reads | Shape it requires |
| --- | --- | --- | --- |
| `TRACE` | AXIS | an array lens | one metric axis free, laid along the chart; optionally one enumerating axis free, drawn as one line per position; every other axis fixed |
| `SERIES` | AXIS | a table lens | one coordinate column laid along the chart, and one numeric attribute column as the value |
| `ANNOTATION` | AXIS | an annotation lens | a space with an axis laid along the chart |
| `POINTS` | VALUES | a table lens | `xColumn`, `yColumn` (and `zColumn`): numeric columns, each row drawn at their values |
| `DISTRIBUTION` | VALUES | a table lens | `valueColumn`, numeric, summarised; optionally `groupColumn` (a text/boolean attribute, label, group id or long-form CHANNEL), one summary per value |

The layers of a VALUES chart are placed in its world, draw one kind, and agree on units per position: `area` (µm²) and
`area_mm2` (mm²) may share x; a volume may not (`assert_value_layer`). Their placement fields
(`asAffine`, `placement`, ...) answer for the rows' place in the world -- for a table's own
space, the identity. A gate -- the rows with `area >= 50` -- is a
table lens window on that column, drawn through like any lens.

**Mark.** How a layer is drawn: `LINE`, `MARKERS`, `LINE_MARKERS`, `STEPS` for a trace or a
series; `MARKERS` or `LINE` (sorted along x) for points; `HISTOGRAM` (with `bins`) or `BOX`
for a distribution (`enums.MARKS`, the first each kind's default). A setting on the layer.
Changing it changes nothing about what the layer reads, which is the test for whether
something is a kind.

**Along axis.** The axis of a layer's source that runs along the chart. It is not stored and
not chosen. It is read from the graph: the path of edges from the source's space to the
chart's world composes into one affine map, that map into a one-axis world is a single row,
and the along axis is the source axis with a coefficient in that row. The coefficient is the
scale, the last entry is the offset, and both are in the unit of the chart's axis.

## Decisions the brief made

- Names: `Chart`, `ChartLayer`, `createChart`, `createChartFromCoordinateSystem`.
- A chart is a composition: it names data by id, owns none, adopts a world.
- The world has one metric axis with a unit, of any metric type. Not assumed to be time.
- Three kinds, defined by what they read. Looks are a style setting.
- Placement uses mikro's strict rule (rfc10), not elektro's reachability gate.
- `chart` imports `core`; `core` never imports `chart`; `chart` does not depend on `Scene`
  or `Layer`.

## Decisions made here

### The world

**An AXIS chart's world has exactly one axis.** A chart has one direction to lay data along;
a second axis in its world would be a direction nothing draws. To chart along `t` of a 4D
timelapse, make the one-axis `t` space and register into it. Values against values draw no world
axis: that is a VALUES chart, whose world says whose rows and whose drawn axes are its columns.

**Metric means SPACE, TIME, MICROTIME or SPECTRUM.** The set is stated in
`core/logic/coords.py` as `METRIC_AXIS_TYPES`, beside `ENUMERATING_AXIS_TYPES` (CHANNEL,
INDEX and CATEGORY), and this app reads both from there. `core` owns them because a dataset's `TRACE` spec
is the same rule asked of a whole dataset: `arrayDatasets(filters: {spec: [TRACE]})` finds
what `createTraceChartLayer` draws whole, and one definition keeps the two from disagreeing.
COORDINATE, DISPLACEMENT and VALUE are not axes data is laid along.

**A world axis must carry a unit.** A unitless axis is a pixel grid's, and a position along it
is an index. A chart over a dataset's own grid is refused; calibrate it into a physical space and
chart that.

**`createChart` takes exactly one of `coordinateSystem` and `axis`, and a `kind`.** Minting from
an axis is
convenience for `createCoordinateSystem` followed by `createChart`, and produces the same two
rows. It uses core's `create_world_space`.

**The world is fixed.** `updateChart` changes name and description. A chart along another
axis is another chart.

### Placement

**No placement column on the layer or the chart.** No axis name, no offset, no scale, no
extent. Everything is read through edges on each request. `tests/test_charts.py` pins this by
replacing a registration with another scale and then another axis, and checking that the
layer row is byte-for-byte unchanged while every answer about it moved.

**The gate is rfc10's, then two chart questions.** `create_layer` calls
`graph.assert_placeable_in`, so the three refusals mikro already distinguishes (nothing
registered, declared unmappable, registered but with no closed form) reach the caller
unchanged. Then, of the same composed map:

1. it must be *total*: it must say where data sits along the chart's axis;
2. it must read that axis from *exactly one* source axis. An oblique map lays a diagonal of
   the data along the chart, and then no axis of the data runs along it.

The strict gate does not ask either question; a scene does not need them.

**The along axis is derived on read and may be null.** If the registration is deleted after
the layer is created, the layer stays, reads `UNREGISTERED`, and has no along axis. This is
the scene behaviour: creation is gated, later edits to the graph are not reverted.

**A sliced lens is placed like any other source.** It has its own space and an edge to its
dataset's grid; the path search crosses it.

### TRACE

**"Free" is an extent above one.** A lens never drops an axis, so nothing names an axis as
free or fixed. A fixed axis is one sliced to a single position.

**The along axis must be metric and free.** One sample along the chart's axis is a point.

**At most one other free axis, and it must be CHANNEL or INDEX.** That is the series axis:
one line per channel, per object. A second free *metric* axis is a surface, which is a
different reading and would be a different kind (out of scope). The series axis is derived
from the lens' shape, not stored.

**`createTraceChartLayer` takes `lens` or `dataset`.** A dataset is read through its unsliced
lens, which is reused if it exists and created if not.

### SERIES

**The coordinate column is not stored.** A table's coordinate columns are the axes of its
space, by name. The column laid along the chart is the along axis, read from the graph.

**The value column is stored, by name.** It is not in the graph: an attribute column has no
graph presence. It must be an ATTRIBUTE column with a numeric DuckDB type. `core` has no
numeric-type helper, so the list is in `chart/logic/chart.py`.

**The value column is inferred only when exactly one qualifies.** With several, taking the
first would chart whichever column was declared first.

### ANNOTATION

**A chart-made drawing surface has two axes: the chart's, and `value` of type VALUE.** So a
mark is drawn at a position and a height, on a trace rather than under it. The edge into the
world is a BY_DIMENSION identity naming only the chart's axis: the two spaces agree about
position, and the world says nothing about height. It is VALIDATED, being exact by
construction.

**VALUE is a new axis type in `core`.** It is allowed only in an annotation collection's
space. A dataset's grid, a table and a unit-carrying space refuse it: data holds its values,
it has no axis of them. It is unitless and is in none of core's axis-type lists.

**The VALUE axis has no unit, because a chart may hold values in several.** Layers in mV and
in arbitrary units can share a chart; the chart commits only to its axis. Which value scale a
mark's height is read against is the viewer's binding. A per-layer drawing surface or a
"which layer's scale" column was not built.

**The collection is reached through the layer's lens.** `ChartLayer.lens` (an annotation lens)
is the only link. Nothing was added to `AnnotationCollection`, so core does not know charts
draw. One layer per lens per chart, by a partial unique constraint: a window over the
collection and its whole lens are two selections and may both be drawn.

**Marks are drawn with core's `createAnnotation(collection:)`.** There is no chart-specific
annotation mutation. `createAnnotationChartLayer` with no collection makes the surface;
calling it again makes another.

**An existing collection can be drawn too**, when the graph places it. Without a VALUE axis
its marks are positions or spans along the chart.

### Value units

A SERIES layer reports `valueUnit` from its column's declaration. A TRACE reports none: mikro
stores no value unit for an array.

### Lifecycle

**Sources are real foreign keys with CASCADE.** Deleting a lens, table or collection takes
the layers that drew it. Deleting a chart or a layer deletes nothing it drew.

**One table, discriminated by `kind`, with a check constraint.** Each kind's row sets exactly
the source its kind names. mikro's `Layer` enforces this in mutations only; here the database
does, as elektro's does.

**`kind` is immutable.** `updateChartLayer` takes style only.

**Deleting a chart needs its creator or an org admin.** A layer is deleted by whoever may
delete its chart.

### Bootstrap

`createChartFromCoordinateSystem` adopts the space and draws what is already laid along it:
arrays (whole), then tables, then annotation collections, up to `policy.nchildren`. It
authors no edges.

**What no kind reads is left out, silently.** An image registered into the space is placed
there and is still not a trace. Scenes have `skipUnplaceable` because a scene bootstrap can
meet residents that fail the gate; here every candidate comes from the strictly placed set,
so the flag would have nothing to act on.

**Only whole arrays are offered.** Which slice of a larger array is the trace is a choice.

## What changed in `core`

All three are neutral: neither names charts, and scenes use the same code.

**`core/logic/compositions.py`: a registry.** `register_composition(model, world_relation,
noun)` and `register_delete_guard(model, guard)`. `deleteCoordinateSystem` and the orphan
sweep read it instead of naming `scenes`; `make_delete` runs the registered guards instead of
taking a `guard=` argument. `CoreConfig.ready()` registers `Scene` and the three picker
guards; `ChartConfig.ready()` registers `Chart`. Scene refusals read exactly as before.

**`core/logic/placement.py`: `PlacementGraph`.** The placement questions (`path`,
`condensed`, `validity`, `invariance`, `state`) asked of a world and a source space. They
were methods of `SceneGraph` that took a layer only to find its source space. `SceneGraph`
is now `PlacementGraph` plus the scene's layer list and pyramid levels; `ChartGraph` is
`PlacementGraph` plus the chart's layer list.

**`VALUE` in `AxisTypeChoices` / `AxisType`**, with its refusals in the two axis writers.

## The boundary

- `chart` imports `core`. `core` imports nothing from `chart`; it learns of charts only
  through the registry, at `ready()`.
- `chart` reads datasets, lenses, tables, annotation collections, spaces and edges. It does
  not name `Scene`, `Layer`, `core.logic.scene`, `core.logic.scene_graph`, the layer
  mutations or `core.render`.
- `visualization` is the one package that names both compositions: `autoVisualize` chooses
  between the two bootstraps there. `Chart` implements `core`'s `Visualization` interface,
  which names no implementer.
- Importing `core.models` or `core.types` *loads* the scene models and types, because they
  share modules with the data models. That cannot be avoided without moving them.

There is no source-parsing test for these rules, by decision. The tests show the boundary by
behaviour: a chart blocks the deletion and the sweep of its world through the registry, and
the registry holds exactly a scene entry and a chart entry.

## The hub

`Chart` is declared in mikro's hub contract (`mikro_server/contract.py`) as the structure
`@mikro/chart`, with a signal on create, update and delete. Its descriptors are
`@mikro/axis_type` and `@mikro/axis_unit` -- what its world axis measures, and in what unit;
`VALUE` and `None` for a VALUES chart, which draws no world axis. The
binding is in `mikro_server/service.py`, beside the scene's; `Chart.descriptors` answers from it.
A chart layer is hosted nowhere, as a scene layer is not.

## Not built, with room left

- **Further kinds** (events, heatmaps, rasters): a new `kind`, a source field in
  `SOURCE_FIELD`, a shape check beside `assert_trace`.
- **Colouring by table attributes**: would add picker columns to `ChartLayer` and a delete
  guard registered with `register_delete_guard`, as scenes do.
- **Mean and spread bands.**
- **Colour by a column on POINTS** (`colorColumn`, a binding like the position columns).
- **Grids, diagrams and boards**: the compositions after scene and chart (`docs/rfc12-compositions.md`).
- **GraphQL subscriptions** for charts and chart layers.
- **The Python client** draws neither yet: it creates charts and reads them.

## Known limits

- A `SERIES` can be laid along SPACE, TIME or INDEX columns only, because those are the only
  axis types a table column may declare. A wavelength or lifetime column cannot be a graph
  axis today.
- Data registered per index (selector-scoped edges) is not placed for a chart layer:
  creation runs the gate without a fixed index.
- A bare BY_DIMENSION that names an axis in nanometres onto one in micrometres states a
  factor of one. core converts units only on axes an edge passes through, not on the ones it
  names. State the factor in the edge.
