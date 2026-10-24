import json
import shutil
import unittest
from pathlib import Path

from appunti_ai.retrieval.lexical import build_index, retrieve


class LexicalRetrievalTests(unittest.TestCase):
    def test_build_reuses_cache_and_retrieves_ranked_passages(self):
        root = Path(__file__).parent / ".tmp_lexical_retrieval"
        shutil.rmtree(root, ignore_errors=True)
        try:
            (root / "extracted").mkdir(parents=True)
            (root / "extracted" / "lecture.md").write_text("# Lezione\n\nRouting IP e subnet mask.\n\nStoria separata.", encoding="utf-8")
            manifest = {"version": "scoped-extraction-v1", "sources": {"lecture.txt": {"status": "completed", "extracted_path": "extracted/lecture.md"}}}
            (root / "extraction_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            first = build_index(root)
            second = build_index(root)
            self.assertEqual(first, second)
            hits = retrieve(first, "routing subnet", limit=1)
            self.assertEqual(len(hits), 1)
            self.assertIn("Routing", hits[0]["text"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_empty_query_is_bounded(self):
        self.assertEqual(retrieve({"entries": []}, "", limit=5), [])
