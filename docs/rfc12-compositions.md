# RFC-12: Compositions — scene, chart, grid, diagram, board

**Status:** Proposed (November 2026). Scene and chart exist; grid, diagram and board are planned
here and not built.
**Builds on:** RFC-11 (a table's space is its frame; scene shows where, chart shows how much),
`core/logic/compositions.py` (the registry every composition joins).

## The one question

A composition draws data. It owns none of it, names it through lenses, and is never lineage.
What tells one kind of composition from another is a single question:

> **What decides where a thing is drawn on screen?**

| Composition | Position on screen comes from | Values are drawn as | World |
|---|---|---|---|
| **Scene** | measured world axes, through the graph's edges | appearance: colour, size, opacity | a place (time or space axes) |
| **Chart** | values -- plus at most one drawn world axis | position | AXIS: one measured axis, drawn; VALUES: whose rows, not drawn |
| **Grid** | enumerating axes: members in an order, no distance | colour per cell | enumerating axes (INDEX, CHANNEL, CATEGORY) |
| **Diagram** | relations, through a layout | appearance on nodes and edges | none: the layout is the position |
| **Board** | other compositions, as panels | -- | none: panels have their own |

Each is a **claim about what screen position means**, checked when a layer is made:

- a scene: *these axes are a place* -- they share a distance, so zooming and turning are honest;
- a chart: *these are quantities to read off* -- each axis is one unit, never rotated;
- a grid: *these are members to look along* -- row 3 is after row 2, not 1.0 units from it;
- a diagram: *these are connections* -- where a node sits means only what it is joined to;
- a board: *these views belong together* -- what is selected in one is selected in all.

## Telling them apart: examples

**One table of nuclei** (`cell_id, y, x, area, volume, condition`, keyed by a mask), five ways:

| | What it shows | Screen position | What `area` is |
|---|---|---|---|
| Scene | the nuclei on the image | `(y, x)` via the table → image edge | the colour of each dot |
| Chart (values) | area against volume | `(area, volume)` | the x position |
| Grid | a gallery of the 50 largest nuclei, cropped | tile index, ordered by `area` | the sort order |
| Diagram | which nuclei touch which | a force layout of the contact network | node size |
| Board | the scene and the scatter side by side | panels | brushing a dot in one highlights it in the other |

**One timelapse** `(t, c, y, x)`:

- *Scene*: the frames in place; `t` is a slider, intensity → colour.
- *Chart* (placed): TRACE, mean intensity against `t` -- `t` is the chart's one world axis.
- *Grid*: a kymograph-free contact sheet, one tile per `(t, c)`: rows are timepoints, columns
  channels, each tile a thumbnail. `t` and `c` are *members* here, in order, with no distance.
- *Diagram*: the lineage tree of the tracked cells, divisions as branches.

**A sparse matrix** (genes × cells):

- *Grid*: a heatmap, genes down, cells across, expression → colour. Neither axis has a metric,
  which is why it is neither a scene nor a chart.
- *Chart* (values): a derived UMAP table drawn as POINTS at `(umap1, umap2)`.

**Where each stops:**

- If rotating the view makes sense → scene. If it would mix units → chart.
- If the axes are *lists* (genes, wells, channels, objects) rather than measurements → grid.
- If nothing about position is measured or listed, only *joined* → diagram.
- If you want two of the above to answer together → board.

## What every composition shares

- A row with `name`, `organization`, `creator`, descriptors (a `Described`), and an optional
  `world` (`CoordinateSystem`, RESTRICT, never owned).
- Layers, each drawing through one `Lens` (`lens_relation`), so `TableDataset.visualizations`,
  a lens' `scenes`, the delete guards and `autoVisualize` extend to a new kind by registering it
  with `register_composition(model, lens_relation=...)`. Core never imports the app.
- The GraphQL `Visualization` interface: `id`, `name`, `descriptors`, `worldCoordinateSystem`.
  A client holding "a visualization" opens it without knowing the kind. The world is the domain
  the layers' rows live in -- drawn by a scene, an AXIS chart and a grid, not by a VALUES chart.
- Column bindings by name, never columns as axes: what a scene reads as appearance, a chart as
  position, a grid as order, a diagram as node style.

## Grid (phase 1)

**Claim:** the screen axes are one or two enumerating axes -- positions are members in an order,
no distance between them.

**World:** a coordinate system whose drawn axes are INDEX, CHANNEL or CATEGORY. A sparse
dataset's own space `(gene, cell)`, a plate's `(row, column)` (CATEGORY rows `A..H`, INDEX
columns `1..12`), or a minted space for a gallery. A CATEGORY axis is where the grid enumerates
by value (`condition`), which is why RFC-11 kept that type.

**Layers:**

| Kind | Reads | Draws |
|---|---|---|
| `HEATMAP` | a sparse lens or an array lens over two enumerating axes | one cell per position, value → colour (`colormap`, `min`, `max`) |
| `TILES` | a table lens (one tile per row) and a source of crops (an array lens + the mask keying the table) | one thumbnail per row, ordered by `orderColumn` (asc/desc), at most `limit` |
| `PLATE` | a table lens whose rows are wells (`rowColumn`, `columnColumn`) and a `valueColumn` | the plate layout, value → colour |

**Checks:** the drawn axes are enumerating (a SPACE axis is refused: that is a scene); a HEATMAP's
lens leaves exactly the two drawn axes free (as TRACE leaves one); `orderColumn` is a column of
the table; a TILES source keys the table (`keyedBy`).

**Order is a binding, not a fact:** sorting tiles by `area` stores `orderColumn: "area"` on the
layer. The grid's axis positions stay members; a different sort is a different layer setting.

**Model:** `grid/` app (`Grid`, `GridLayer` with `kind`, `lens`, settings), `createGrid`,
`createHeatmapGridLayer`, `createTilesGridLayer`, `createPlateGridLayer`; `autoVisualize` sends
a sparse dataset (today refused: neither place nor quantity) to a grid.

**Tests:** a sparse lens shown as a heatmap; a SPACE world refused with "that is a scene"; tiles
of a keyed table ordered by a column; `visualizations` lists the grid.

## Board (phase 2)

**Claim:** these compositions belong together, and a selection in one is a selection in all.

**Model:** `Board` (no world) with `BoardPanel` rows: `composition` (a scene, chart, grid or
diagram -- one nullable FK each, a check constraint for exactly one), a position (`row`, `col`,
`rowSpan`, `colSpan` in a panel grid), and `links`:

| Link | Means | Holds when |
|---|---|---|
| `SELECTION` | brushing rows in one panel selects them in the others | the panels draw lenses of one table, or tables keyed to each other |
| `CAMERA` | panning/zooming one scene moves the other | both scenes share a world |
| `AXIS` | two charts share x (or y) | their bound columns' units convert (or their world axis is one) |

**Checks:** a link is refused when it cannot hold -- a selection between two unrelated tables, a
camera between two worlds. Selection is a client state; a *persisted* selection is a gate (a
table lens window), which is already a lens and can be named, derived from, and drawn.

**Not lineage, not a lens holder:** a board draws nothing itself; it is listed beside the
compositions it holds (`Scene.boards`, `Chart.boards`), and deleting a panel's composition
removes the panel.

**Tests:** a board of a scene and a chart of one table, linked by selection; a camera link
between two worlds refused; deleting a chart drops its panel.

## Diagram (phase 3)

**Claim:** where a node sits means only what it is joined to.

**Layers:**

| Kind | Reads | Draws |
|---|---|---|
| `NETWORK` | a network lens (a traced neuron, a contact graph) | its nodes and edges by a `layout` (FORCE, LAYERED, RADIAL) |
| `TREE` | a table lens with a parent column (`parentColumn`), e.g. cell divisions | a tree; optionally one placed axis (`t`) for depth, as a chart allows one |
| `LINEAGE` | a container | the saved view of its lineage walk |

**Position is a layout, not data:** the layout kind and its seed are stored; node coordinates are
the client's to compute. A TREE that lays depth along `t` is the one case with a measured axis,
and it follows the chart's rule: at most one, from the graph.

**Tests:** a network lens shown as a FORCE diagram; a tree from a tracks table with a parent
column; a TREE's time axis must be measured with a unit.

## Order of work

1. **Grid** -- the gap in what can be shown today (sparse data has nowhere honest to go).
2. **Board** -- linking scene and chart is the most asked-for workflow once both exist.
3. **Diagram** -- network and lineage views exist as queries already; a saved view is last.

Each phase is one app beside `chart/`, registered with `register_composition`, with its own
GraphQL types implementing `Visualization`, its own tests, and a client regeneration.

## Open questions

- **Grid order across layers:** two TILES layers in one grid, sorted differently -- refuse, or
  let the first layer's order win?
- **Board persistence of view state:** store each panel's camera/zoom, or leave it to the client
  like a scene's current view?
- **Diagram layouts server-side:** compute positions on the server (deterministic, cacheable) or
  only on the client (cheap, unstable between viewers)?
