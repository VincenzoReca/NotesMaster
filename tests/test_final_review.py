import unittest

from appunti_ai.consolidation.notes import preserve_image_blocks, validate_consolidated_content


class FinalReviewTests(unittest.TestCase):
    def test_image_block_is_restored_when_consolidation_drops_it(self):
        chunks = [{"text": "Explanation.\n\n![Source figure](images/figure.png)"}]
        reviewed = preserve_image_blocks("# Chapter\n\nExplanation.", chunks)
        self.assertIn("![Source figure](images/figure.png)", reviewed)

    def test_review_validation_rejects_placeholders(self):
        result = validate_consolidated_content("# Chapter\n\n[DA VERIFICARE]", [{"text": "source text"}])
        self.assertEqual(result["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
