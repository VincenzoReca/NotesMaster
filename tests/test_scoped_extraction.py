import unittest
from pathlib import Path

from appunti_ai.contracts.paths import discover_subject
from appunti_ai.extraction.scoped import ExtractionError, build_scope, extract_scope


class ScopedExtractionTests(unittest.TestCase):
    FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "pipeline_paths" / "valid"

    def test_extracts_a_single_section_and_reuses_the_matching_hash(self):
        subject = discover_subject(self.FIXTURE_ROOT, "Reti")
        scope = build_scope(subject, "02_Indirizzamento", shared=False)

        first_manifest = extract_scope(scope)
        first_record = first_manifest["sources"]["source.txt"]
        self.assertEqual(first_record["status"], "completed")
        extracted_path = scope.output_root / first_record["extracted_path"]
        self.assertTrue(extracted_path.is_file())
        self.assertTrue((scope.output_root / "audit" / "extraction_audit.json").is_file())

        second_manifest = extract_scope(scope)
        self.assertEqual(second_manifest["sources"]["source.txt"], first_record)

    def test_shared_scope_uses_its_own_pages_directory(self):
        subject = discover_subject(self.FIXTURE_ROOT, "Reti")
        scope = build_scope(subject, None, shared=True)

        manifest = extract_scope(scope)

        self.assertEqual(scope.visual_dir.name, "pages")
        self.assertEqual(manifest["scope_kind"], "shared")
        self.assertEqual(manifest["sources"]["shared.txt"]["status"], "completed")

    def test_rejects_ambiguous_scope_selection(self):
        subject = discover_subject(self.FIXTURE_ROOT, "Reti")
        with self.assertRaises(ExtractionError):
            build_scope(subject, "02_Indirizzamento", shared=True)

    def test_media_is_reported_explicitly_without_silent_skip(self):
        subject = discover_subject(self.FIXTURE_ROOT, "Reti")
        manifest = extract_scope(build_scope(subject, "02_Indirizzamento", shared=False))
        record = manifest["sources"]["lezione.mp4"]
        self.assertEqual(record["status"], "unsupported_media")
        self.assertIn("decoder", record["diagnostic"])


if __name__ == "__main__":
    unittest.main()
