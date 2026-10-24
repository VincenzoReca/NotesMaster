import unittest

from appunti_ai.consolidation.notes import coverage_audit


class ContentCoverageTests(unittest.TestCase):
    def test_reports_low_coverage_for_unrepresented_chunk(self):
        result = coverage_audit("# Routing\n\nRouting forwards packets.", [{"source": "a", "text": "Routing forwards packets."}, {"source": "b", "text": "Multicast addressing uses groups and membership."}])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["low_coverage_chunks"], 1)

    def test_accepts_represented_terms(self):
        result = coverage_audit("IPv4 addressing uses network identifiers and host addresses.", [{"source": "a", "text": "IPv4 addressing, network identifiers, host addresses."}])
        self.assertEqual(result["status"], "passed")
