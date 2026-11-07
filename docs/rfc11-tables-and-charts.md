# RFC-11: Tables, axes and charts — what is a space, and what is a value

**Status:** Accepted and implemented (November 2026).
**Builds on:** RFC-5 (coordinate systems, transformation kinds), RFC-8 (a spatial fact is a
node or an edge), RFC-10 as amended by `by-dimension-parts.md` (placement must condense), the
chart app (`chart/DESIGN.md`).
**Leaves untouched:** RFC-7's boundary (a key out of a table is a `Column.references`, never a
FIELD), every scene, every placed chart, every edge.
**Next:** `docs/rfc12-compositions.md` (grids, diagrams, boards).

## The question

A table of per-object measurements has `area`, `volume`, `mean_intensity` and `condition`. A
user wants area against volume, the distribution of `area` once per `condition`, and the rows
with `area >= 50` as a selection they can derive data from. Before this RFC none of it could
be said: a chart laid layers along exactly one world axis, and a table reached it only through
its coordinate columns.

## The answer in one sentence

**A table's space is its frame; its measurements are values; a scene shows where, a chart
shows how much.**

## First principles

1. **An array is a function on a grid; a table is a marked point set.** An array's space is
   its grid `(c, z, y, x)` and its intensity is a value at each position, never an axis. A
   table is the same shape: its **frame** -- the coordinate columns `(t, y, x)`, or `cell_id`,
   or the synthetic `object` -- is where a row sits, and `area`, `volume`, `photons` are values
   the row carries. So a table's coordinate system is its frame and nothing else.
2. **A coordinate system assumes nothing.** It is a product of axes. "Spatial" and "plottable"
   are claims a consumer makes and checks:
   - a scene checks that its world is a place (a time or space axis);
   - a chart checks that it has one measured axis, or that the columns it binds are numbers;
   - a transformation part checks what its kind needs of the axes it touches.
3. **Structure lives on the axes.** Each axis is *measured* (SPACE, TIME, MICROTIME, SPECTRUM)
   or *enumerating* (CHANNEL, INDEX, CATEGORY). A **metric group** spans axes: the ones that
   share one distance. A SPACE axis is in `"space"` unless it says otherwise; every other axis
   shares a distance with nothing until it declares a group (two spectral channels of one
   detector, a phasor plane). Units cannot infer a group: a wavelength in nm has the dimension
   of y in µm and is not a spatial direction.
4. **Each operation states what it needs:**

   | Operation | Needs of the axes it touches |
   |---|---|
   | relabelling, a FIELD key, an IDENTITY part over a category | enumerating is enough |
   | SCALE, TRANSLATION, AFFINE | measured (never a CATEGORY) |
   | ROTATION, isometry, distance, a scene's render plane | the axes it *mixes* share one metric group |

## Scene and chart: one question tells them apart

**What decides where a thing is drawn on screen?**

| | Scene | Chart |
|---|---|---|
| Answers | *where* is it? | *how much*, and how do values relate? |
| Screen position comes from | the graph: edges place each layer in the world | the data: values -- plus at most one placed axis |
| Values are drawn as | appearance: colour, size, opacity | position |
| World | required: a place, time or space axes | optional: one measured axis, or none |
| Screen axes | world axes sharing one distance | each its own unit |
| You | navigate (pan, zoom, rotate) | read (ticks, ranges, brushing) |

**Rule of thumb:** if rotating the view makes sense, it is a scene; if rotating would mix units
(µm² into µm³), it is a chart.

The same lens, both ways:

```
lens: nuclei[area ≥ 50]          (a table lens: a gate on a measurement)

Scene  "where are the big nuclei?"       rows at their (y, x), placed by edges; colour by `area`
Chart  "how does area relate to volume?" rows at (area, volume); their place unused
```

```
lens: timelapse[c=0, y=40, x=12]   (an array lens, `t` left free)

Scene  the frames in place (t, y, x); intensity → colour
Chart  TRACE: intensity(t), with t the chart's one placed axis
```

What both share: they own no data, draw through lenses, are listed by
`TableDataset.visualizations` and a lens' `scenes`, and are never lineage. A column is read by
name in both and never becomes an axis: a scene's `colorBys`/`sizeColumn` read it as
appearance, a chart's `xColumn`/`valueColumn` as position -- one binding, a different channel.

## The model

### Tables
- `TableDataset.coordinate_system` is the frame: the coordinate columns (SPACE, TIME, INDEX,
  **CHANNEL**), else a synthetic `object`. Measurements stay columns.
- **Long form.** A per-channel measurement written one row per (object, channel) declares the
  channel a CHANNEL coordinate: `(cell, c, volume, mean)` says what the wide `(cell, mean_dapi,
  mean_gfp)` says, with the channel an axis an image's `c` can be identified with. It is
  unitless. A data column declares `variesOver` -- the CHANNEL coordinates its value changes
  along -- so a reader counts a per-cell `volume` once per cell, not once per channel.
  Undeclared means every CHANNEL axis, which counts nothing twice wrongly.
- **Keys over a long-form table.** A mask keying `(cell, c)` supplies `cell`; the channel is the
  reader's to pick (`graph.reader_selected_axes`), or passes through by name from a source with
  a `c` of its own -- the image the means were measured in. Attribute plans skip the former (an
  id alone finds a row per channel: a slice, not a row) and bind the latter like `t`.
  `TableDataset.keyedBy` reads what a key produces and passes off the edge's own parts.

### Gates
A **gate** is a table lens window on a numeric column: `{axis: "area", min: 50}`. A window
keeps the rows whose value lies in `[min, max]`; on a coordinate it also bounds a region of
space, on a measurement it bounds nothing in space. It is a lens like any other -- one row per
selection, intersected by sub-lenses, normalised by `merge_duplicate_lenses` against the same
names it was cut with (`coordinate_system.windowable_names`), a row filter on read
(`windowFilters`), and a `derivedFrom` source. A text column has no bounds and cannot be
windowed: a layer's `filterBys` filters by value.

### Charts
Every chart has a **world -- the domain its layers' rows live in** -- and every layer is placed
into it. A chart draws at most one axis of it (`Chart.kind`):

| Kind | World | Drawn? | A layer's placement says |
|---|---|---|---|
| `AXIS` | one measured axis with a unit, e.g. `t` | yes, horizontally | where along `t` each value sits |
| `VALUES` | whose rows: a table's space -- `cell_id`, `object`, a localization table's `(t, y, x)` | no | which rows are the same rows |

- **AXIS charts** lay TRACE, SERIES and ANNOTATION along their axis (`asAffine` is one row).
  Unchanged.
- **VALUES charts** (`createChart(kind: VALUES, coordinateSystem: <a table's space>)`) draw
  POINTS -- a table lens' rows at the values of `xColumn`, `yColumn` and optionally `zColumn` --
  and DISTRIBUTION -- `valueColumn` as a HISTOGRAM (`bins`) or a BOX, once per value of an
  optional `groupColumn` (a text or boolean attribute, a label, a group id, or a long-form
  CHANNEL). A layer's lens must be placed in the world (its own table's space, or one its table
  is derived or registered into); every bound column exists and holds numbers. Neither their
  kinds nor their units need agree (a scatter beside a histogram): the chart's remembered `layout` lays them
  out -- STACKED, a panel per layer; SHARED, layers in the same units overlaid and the rest
  beside them -- so `area` against `volume` and `cluster_id` against `arclength` share a chart
  without sharing an axis.
- **Same world, same rows.** A PCA written as a table derived from the measurements with
  IDENTITY over the frame is placed in their world, so `pc1` and `area` draw in one VALUES chart,
  and a board can link selection between them (RFC-12).
- **Why every table has an index, in practice:** a table's space is how its rows are addressed --
  an INDEX for measurements, the frame for localizations. No `object` axis is added beside a
  frame: it would put an extra axis into every edge out of the table, and a row number does not
  identify rows across tables -- a declared id does.
- A chart is AXIS or VALUES, never both: a position along a world axis and a measurement are not
  one kind of thing, even in one unit.
- `createTablePlot(table, lens?, kind, xColumn, yColumn?, zColumn?, groupColumn?, bins?)` makes a
  VALUES chart over the table's space and its layer in one transaction. `autoVisualize` of a
  table lens whose table has no place draws its first two measurements against each other.
- Descriptors: a VALUES chart describes `@mikro/axis_type` as `VALUE` (it draws no world axis).

### PCA, UMAP, clusters
A computation over a table's columns writes a **derived table** whose rows are the source's
rows: the same frame (`cell_id`), `derivedFrom` the source (or a gate of it) with IDENTITY over
the frame, an `identifiedBy: TABLE` key back to the source, and the loadings or parameters in
`provenance_metadata`. Its columns (`pc1`, `pc2`) are plotted like any column, and a mask's
hover reaches them through the join walk. Applying the same PCA to another table is running the
action again: a computation recorded, not a geometric relation claimed.

## Considered and rejected

**Every column an axis** (built, tested green, discarded). A table's space as the product of all
its columns makes gates windows and plots name the table's own axes, but: every edge out of a
table must name its frame (a CATEGORY refuses whole metric edges, an IDENTITY needs equal
names), two tables over one image no longer share a space shape, a 2000-column CellProfiler
table becomes a 2000-axis space carried into every placement, and the frame comes back anyway
as "the axes that are not FEATURE or CATEGORY", filtered at every key and plan site. And the
only thing a chart needed from it -- columns as positions -- a chart can name directly.

**A FeatureLens per table** (built first, discarded). A second lens kind owning a space of one
FEATURE axis per column. It split one table into two readings that a user could not tell apart
("a table lens and a feature lens are both selections of the same rows"), and it existed only to
give charts axes.

**A placing FIELD out of the table's own space.** Breaks RFC-7 (a FIELD whose field is a table),
and `(i)` is an enumerating domain the rank check exists to keep out of geometry.

With columns staying columns there is no PCA *edge*: no FEATURE axis type, no feature space, no
scene over a declared group of components. CATEGORY stays (a grid will enumerate by value, RFC-12).

## What stays refused

- A CATEGORY with a unit, on an array's grid, or under arithmetic (whole metric edges and
  SCALE/TRANSLATION/AFFINE parts); a BY_DIMENSION passes it with an IDENTITY part.
- A metric group on an enumerating axis, or across dimensions.
- A ROTATION mixing axes outside one metric group (use AFFINE).
- A CHANNEL coordinate with a unit; `variesOver` on a coordinate or naming no CHANNEL coordinate.
- A window on a text column.
- A value layer in an AXIS chart, a placed layer in a VALUES chart, two value kinds in one chart, and
  a value layer whose table is not placed in the VALUES chart's world.

## Where it lives

`core/logic/coords.py` (axis sets, metric groups, `ColumnSpec.varies_over`), `core/logic/graph.py`
(`reader_selected_axes`, the rotation rule, key coverage, `table_keys`),
`core/logic/coordinate_system.py` (`windowable_names`, `write_key_edges`),
`core/mutations/table_dataset.py` (CHANNEL, `variesOver`), `chart/logic/chart.py`
(`assert_value_layer`, `create_layer`), `chart/logic/table_plot.py`, `visualization/logic.py`.
Tests: `test_axis_structure.py`, `test_table_space.py`, `test_chart_values.py`,
`test_table_plot.py`.
