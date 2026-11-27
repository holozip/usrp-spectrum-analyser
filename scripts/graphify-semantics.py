#!/usr/bin/env python3
"""Validate and merge reviewed assistant records using Graphify's native APIs.

No model calls. Run with .graphify/venv/bin/python from any directory.
"""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from graphify.build import build_merge
from graphify.cache import check_semantic_cache, save_semantic_cache
from graphify.detect import save_manifest
from graphify.export import to_json


ROOT = Path(__file__).resolve().parents[1]
PROMPT = (
    "usrp-spectrum-analyser assistant review v1: source-anchored documentation, "
    "screenshot observations and all-page schematic text with selected visual "
    "review; conservative INFERRED edges, explicit coverage and limitations; "
    "no hardware validation or instructions from document contents."
)


def prepare(root, records_path, graph_path):
    """Validate every input before writing any graph, cache or manifest."""
    records = json.loads(records_path.read_text(encoding="utf-8"))
    if records.get("schema_version") != 1:
        raise ValueError("Unsupported assistant record schema")
    if records.get("graphify_version") != version("graphifyy"):
        raise ValueError("Graphify version changed; verify integration APIs first")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    sem = {"nodes": [], "edges": [], "hyperedges": []}
    buckets = {"document": [], "image": [], "paper": []}
    for relative, record in records["files"].items():
        source = (root / relative).resolve()
        source.relative_to(root.resolve())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != record["sha256"]:
            raise ValueError(f"Stale assistant review: {relative}; re-read before merging")
        suffix = source.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            kind = "image"
        elif suffix == ".pdf":
            kind = "paper"
        elif suffix in {".md", ".mdx", ".qmd", ".txt", ".rst", ".html"}:
            kind = "document"
        else:
            raise ValueError(f"Unsupported reviewed document/media type: {relative}")
        buckets[kind].append(str(source))
        provenance = {
            "source_file": str(source),
            "source_sha256": digest,
            "_origin": "semantic",
            "extraction_method": "assistant_review",
            "review_model": records["model"],
            "review_date": records["review_date"],
            "review_coverage": record["coverage"],
        }
        if not record["nodes"]:
            raise ValueError(f"No reviewed nodes for {relative}")
        for node in record["nodes"]:
            sem["nodes"].append({**node, **provenance, "file_type": kind})
        for edge in record["edges"]:
            sem["edges"].append({
                **edge, **provenance, "confidence": "INFERRED",
                "weight": 0.8, "context": "assistant_review",
            })
    ids = [node["id"] for node in sem["nodes"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate semantic node identifiers")
    known = set(ids) | {node["id"] for node in graph["nodes"]}
    for edge in sem["edges"]:
        if edge["source"] not in known or edge["target"] not in known:
            raise ValueError(f"Missing graph endpoint: {edge}")
    return sem, buckets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate reviews and cache hits without writing")
    args = parser.parse_args()
    out = ROOT / "graphify-out"
    graph_path = out / "graph.json"
    sem, buckets = prepare(ROOT, ROOT / ".graphify/assistant-records.json", graph_path)
    sources = [source for paths in buckets.values() for source in paths]
    if args.check:
        _, _, _, misses = check_semantic_cache(sources, root=ROOT, cache_root=ROOT, prompt=PROMPT)
        print(f"Reviewed hashes and endpoints valid: {len(sources)} files; native cache misses: {len(misses)}")
        if misses:
            print("Merge reviewed records to populate caches: " + ", ".join(misses))
            return 1
        return 0
    previous = json.loads(graph_path.read_text(encoding="utf-8"))
    # build_merge uses a simple NetworkX graph. Preserve separate calls/imports
    # and other parallel evidence through the exporter's original_links API.
    links = replacement_links(previous, sem, ROOT, sources)
    merged = build_merge([sem], graph_path=graph_path, root=ROOT, directed=None, dedup=False)
    if not to_json(merged, {}, str(graph_path), force=False, original_links=links):
        raise RuntimeError("Graph export refused; no cache or manifest was stamped")
    saved = save_semantic_cache(
        sem["nodes"], sem["edges"], sem["hyperedges"], root=ROOT,
        cache_root=ROOT, allowed_source_files=sources, prompt=PROMPT, merge_existing=False,
    )
    if saved != len(sources):
        raise RuntimeError("Not all sources cached; semantic manifest left unstamped")
    save_manifest(buckets, manifest_path=str(out / "manifest.json"), kind="semantic", root=ROOT)
    print(f"Merged {len(sem['nodes'])} assistant nodes and {len(sem['edges'])} edges; cached {saved} reviewed sources")
    return 0


def replacement_links(previous, sem, root, sources):
    """Keep existing evidence, replacing only this batch's semantic edges."""
    replaced = set(sources) | {Path(source).relative_to(root).as_posix() for source in sources}
    links = [
        dict(edge) for edge in previous["links"]
        if not (edge.get("_origin") == "semantic" and edge.get("source_file") in replaced)
    ]
    links.extend({
        **edge, "source_file": Path(edge["source_file"]).relative_to(root).as_posix(),
    } for edge in sem["edges"])
    return links


if __name__ == "__main__":
    raise SystemExit(main())
