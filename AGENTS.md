# Codebase navigation

This repository has an optional local Graphify index at `graphify-out/graph.json`,
exposed by the project-scoped `graphify` MCP server in `opencode.jsonc`.

- Use focused graph queries for unfamiliar symbols, RTL hierarchy and related
  documentation. Read the cited source before editing; ordinary grep/read remains
  appropriate for small, known-file tasks. Do not load the whole graph into context.
- The server reloads the graph, not source files. Before relying on code results
  after edits, run `GRAPHIFY_BACKEND=none .graphify/venv/bin/graphify update . --no-cluster`.
  Do not use `extract --code-only`: this trial includes docs, screenshots and PDFs.
- Check document/media content hashes against the assistant extraction records
  before relying on interpretations. Re-read changed files and refresh their
  semantic records; a code update alone does not interpret images or PDFs.
  See `docs/graphify.md` for the refresh workflow and limitations.
- For RTL impact, explicitly include the `instantiates` relation in `affected`.
  The default relation set omits it. The graph is not an elaborated HDL design:
  it does not establish instance paths, signal flow, clock domains or timing.
- Treat assistant-derived document/image edges as interpretation, not AST facts.
  Missing graph edges do not prove a change is safe. HTTP, binary formats, FPGA
  registers, callbacks and native UHD behaviour need source/document verification.
- Generated `hbdec1.v`/`hbdec2.v` internals are excluded. Do not blanket-exclude
  `rtl/sim/`: `axi_fifo_2clk_sim.v` is also used by the synthesis project.
- Keep `.aws`, dependencies, generated builds, `.graphify/` and `graphify-out/`
  out of the index and version control. Never put secrets into the graph.
- Do not enable paid/automatic semantic providers, Git hooks or global Graphify
  integrations without approval. Use the existing running radio API for receiver
  operations; do not open the radio from a second process merely to inspect it.
