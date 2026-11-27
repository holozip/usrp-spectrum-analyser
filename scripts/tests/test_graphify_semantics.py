"""Guard the local assistant-record integration without touching the real graph."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "graphify_semantics", Path(__file__).resolve().parents[1] / "graphify-semantics.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AssistantRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "doc.md").write_text("# Evidence\n", encoding="utf-8")
        self.records_path = self.root / "records.json"
        self.graph_path = self.root / "graph.json"
        self.graph_path.write_text(json.dumps({"nodes": [{"id": "code"}]}), encoding="utf-8")
        self.records = {
            "schema_version": 1, "graphify_version": "0.9.121",
            "model": "test", "review_date": "2026-11-27",
            "files": {"doc.md": {
                "sha256": hashlib.sha256((self.root / "doc.md").read_bytes()).hexdigest(),
                "coverage": "Whole test document",
                "nodes": [{"id": "doc", "label": "Evidence", "source_location": "L1"}],
                "edges": [{"source": "doc", "target": "code", "relation": "documents"}],
            }},
        }

    def prepare(self):
        self.records_path.write_text(json.dumps(self.records), encoding="utf-8")
        return MODULE.prepare(self.root, self.records_path, self.graph_path)

    def test_valid_records_keep_semantic_provenance_and_code_reference(self):
        sem, buckets = self.prepare()
        self.assertEqual(len(sem["nodes"]), 1)
        self.assertEqual(sem["nodes"][0]["_origin"], "semantic")
        self.assertEqual(sem["nodes"][0]["file_type"], "document")
        self.assertEqual(sem["edges"][0]["target"], "code")
        self.assertEqual(sem["edges"][0]["confidence"], "INFERRED")
        self.assertEqual(sem["edges"][0]["context"], "assistant_review")
        self.assertEqual(buckets["document"], [str(self.root / "doc.md")])

    def test_stale_hash_is_rejected_without_changing_graph(self):
        original = self.graph_path.read_bytes()
        (self.root / "doc.md").write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Stale assistant review"):
            self.prepare()
        self.assertEqual(self.graph_path.read_bytes(), original)

    def test_missing_code_reference_is_rejected(self):
        self.records["files"]["doc.md"]["edges"][0]["target"] = "missing"
        with self.assertRaisesRegex(ValueError, "Missing graph endpoint"):
            self.prepare()

    def test_duplicate_node_identifier_is_rejected(self):
        nodes = self.records["files"]["doc.md"]["nodes"]
        nodes.append(dict(nodes[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate semantic node"):
            self.prepare()

    def test_version_change_requires_api_review(self):
        self.records["graphify_version"] = "future"
        with self.assertRaisesRegex(ValueError, "Graphify version changed"):
            self.prepare()

    def test_source_outside_project_is_rejected(self):
        self.records["files"]["../outside.md"] = self.records["files"].pop("doc.md")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_parallel_code_evidence_survives_semantic_replacement(self):
        sem, _ = self.prepare()
        previous = {"links": [
            {"source": "a", "target": "b", "relation": "calls", "_origin": "ast"},
            {"source": "a", "target": "b", "relation": "imports", "_origin": "ast"},
            {"source": "old", "target": "code", "_origin": "semantic", "source_file": "doc.md"},
        ]}
        links = MODULE.replacement_links(previous, sem, self.root, [str(self.root / "doc.md")])
        self.assertEqual(len(links), 3)
        self.assertEqual([edge.get("relation") for edge in links[:2]], ["calls", "imports"])
        self.assertEqual(links[-1]["source_file"], "doc.md")


if __name__ == "__main__":
    unittest.main()
