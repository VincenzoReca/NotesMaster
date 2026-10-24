import unittest

from appunti_ai.consolidation.notes import validate_consolidated_content


class ContentValidationTests(unittest.TestCase):
    def test_validates_structure_and_flags_human_review(self):
        result = validate_consolidated_content("# IPv4\n\nAn IPv4 address identifies a network interface and is divided into network and host parts.", [{"text": "IPv4 address terminology and network part."}])
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["human_review_required"])

    def test_flags_process_markers(self):
        result = validate_consolidated_content("# Notes\n\n[truncated]", [{"text": "A sufficiently long source passage."}])
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("process_or_truncation_marker", result["issues"])
