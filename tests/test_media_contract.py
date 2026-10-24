import unittest
from pathlib import Path

from appunti_ai.extraction.media import media_kind, unsupported_media_record


class MediaContractTests(unittest.TestCase):
    def test_media_contract_preserves_kind_and_temporal_provenance(self):
        path = Path("lecture.mp4")
        self.assertEqual(media_kind(path), "video")
        record = unsupported_media_record(path, "abc")
        self.assertEqual(record["status"], "unsupported_media")
        self.assertEqual(record["media_kind"], "video")
        self.assertEqual(record["provenance"]["time_ranges"], [])
