# Local Graphify trial

This is an optional navigation index, not a source of truth or a hardware
verification tool. Graphify **0.9.121** is installed in `.graphify/venv` and exposed
over local stdio by the project-only `graphify` MCP server in `opencode.jsonc`.
No global configuration, Git hooks, automatic model calls or radio access is
required. The three Graphify PR tools are denied; GitHub work still uses `gh`.

## Installation

From the repository root, recreate the ignored environment if necessary:

```bash
snip uv venv --python 3.13 .graphify/venv
snip uv pip install --python .graphify/venv/bin/python 'graphifyy[mcp,pdf]==0.9.121'
```

Do not recreate an existing environment unnecessarily. OpenCode reloads the
project configuration automatically. Other projects do not inherit this server.

## Structural refresh

```bash
snip env GRAPHIFY_BACKEND=none GRAPHIFY_MAX_WORKERS=4 .graphify/venv/bin/graphify update . --no-cluster
```

This parses supported code and Markdown headings/links locally, without a model.
Do **not** replace it with `extract --code-only`: code-only scope excludes new
documents and persists across later updates. Do not use backend auto-detection;
it can choose cloud credentials from the environment.

MCP reloads changes to `graph.json`; it does not rebuild changed sources itself.
No watcher is left running. A matching Git commit does not establish freshness
against uncommitted files.

Use the raw `--no-cluster` update path to preserve parallel relationships. To
produce a report afterwards without model calls or an HTML/CDN dependency:

```bash
snip env GRAPHIFY_BACKEND=none .graphify/venv/bin/graphify cluster-only . --no-label --no-viz
```

## Assistant document and media refresh

The initial reviewed records are in the ignored local artifact
`.graphify/assistant-records.json`. Each original file has a SHA-256, explicit
coverage and a complete replacement set of semantic nodes/edges. Records cite
Markdown lines, PDF pages or image regions. All assistant edges are conservatively
marked `INFERRED` with `context=assistant_review`; `_origin=semantic` keeps them
separate from parsed source. Graphify's confidence scores are categories, not
measured probabilities.

Check reviewed hashes, code endpoints and native cache hits without writing:

```bash
snip env GRAPHIFY_BACKEND=none .graphify/venv/bin/python scripts/graphify-semantics.py --check
```

For changed or newly added documents/media, ask an assistant to read the originals
and update the records, their hashes and coverage. Preserve complete per-source
records; do not stamp a partly reviewed file as complete. Then merge the reviewed
records, cache them natively and stamp only their semantic manifest rows:

```bash
snip env GRAPHIFY_BACKEND=none .graphify/venv/bin/python scripts/graphify-semantics.py
snip env GRAPHIFY_BACKEND=none .graphify/venv/bin/graphify cluster-only . --no-label --no-viz
```

The helper makes no model calls and refuses stale hashes, missing code endpoints,
duplicate semantic IDs, source paths outside the project or an unreviewed package
version change. It uses Graphify's native cache, merge and export APIs, preserving
parallel code relationships with `original_links`. A structural update preserves
existing interpretations but **does not import semantic caches or reinterpret
changed documents**. It may also preserve rather than regenerate Markdown
headings for a document with a semantic layer. Check/re-read stale originals
before relying on those results.

The native cache prompt fingerprint is fixed in the helper. Saving caches alone
does not add records to the graph. If generated output is lost, run the structural
refresh, replay the reviewed records, then regenerate the report. If the local
records are also lost, the assistant review must be repeated.

Run the integration guard tests with:

```bash
snip .graphify/venv/bin/python -m unittest discover -s scripts/tests -p 'test_graphify_semantics.py' -v
```

## Queries

Use the MCP tools through Code Mode, or these CLI examples from the repository
root:

```bash
snip .graphify/venv/bin/graphify query useFrequencyGestures --context call --depth 1 --budget 1000
snip .graphify/venv/bin/graphify query BookmarkStore --context call --depth 1 --budget 1000
snip .graphify/venv/bin/graphify affected axi_fifo_2clk --relation instantiates --depth 2
```

`instantiates` is deliberately explicit: Graphify's default impact relation set
omits RTL instantiations. Narrow traversal rather than trusting token budgets as
hard caps; Graphify can return all edges over budget. Read cited source before
making changes.

## Scope and limitations

- Includes Python, React JSX/JavaScript, RTL, tests and supported scripts, plus
  Markdown documentation and assistant-interpreted media.
- `.graphifyignore` excludes credentials, dependencies, builds and the generated
  Xilinx `hbdec1.v`/`hbdec2.v` internals. References to excluded modules can remain
  unresolved. The synthesis-used `rtl/sim/fifo/axi_fifo_2clk_sim.v` stays included.
- CAD `.DWG`/`.step`, firmware binaries, Vivado `.xpr`/`.xci`, constraints `.xdc`,
  memory `.mem` and Tcl `.tcl` files are not directly parsed. Documentation may
  describe them, but that does not make their contents indexed or validated.
- RTL results are module-type relationships, not elaborated instance paths,
  port/signal flow, clock domains, timing or synthesis results.
- HTTP, binary formats, FPGA register protocols, React callbacks and native UHD
  behaviour require source and document checks across boundaries. Missing edges
  do not mean that a change is safe.
- A dashboard screenshot shows a captured UI, not live device state or RF
  validation. A schematic identifies documented hardware, not the loaded FPGA
  image or measured behaviour of this particular device.

## Privacy and generated files

Code/Markdown structural parsing and MCP run locally. The user explicitly chose
the current assistant's cloud model to interpret documentation, screenshots and
PDFs for this initial trial. Graph excerpts supplied to a cloud assistant also
leave the machine through that assistant. No separate API key is stored.

`.graphify/` contains the isolated environment and local extraction records.
`graphify-out/` contains the generated graph, caches and reports. Both are ignored
by Git and excluded from indexing. Treat graphs as potentially containing source
information; do not publish them automatically. Do not commit or push this setup
without separate approval.

## Initial document and media coverage

The assistant reviewed `README.md`, `docs/api.md`, `docs/firmware-build.md`,
`docs/dashboard.webp` and `docs/hardware-schematic/USRP_B210.pdf`. All 12 schematic
pages were text-extracted and their sheet identities reviewed; pages 4, 7, 8 and
12 were additionally rendered and visually inspected. This was a selected
component/net review, not an exhaustive electrical or pinout audit. Embedded PDF
JavaScript was not executed. The screenshot does not show the bookmark UI.

The review found an unresolved identifier discrepancy: the schematic labels U7
`XC7K325T-2FBG676I`, while the README and Vivado project specify
`xc7k325tffg676-2`. Both identifiers are retained in the graph; no package
equivalence, incompatibility or safe-image conclusion is asserted.

Mechanical CAD files were inventoried but their geometry is not indexed.
Performance figures are attributed to the firmware document, not remeasured.
Candidate TX, dual-channel RX and external clocking remain unverified according
to that document.
