import unittest
from pathlib import Path

from appunti_ai.contracts.paths import (
    PathContractError,
    discover_subject,
    discover_subject_names,
    ensure_within,
)


class PipelinePathsTests(unittest.TestCase):
    FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pipeline_paths"

    def fixture(self, name):
        return self.FIXTURES / name

    def test_discovers_only_immediate_sections_in_numeric_order(self):
        root = self.fixture("valid")
        subject = root / "input" / "Reti"

        layout = discover_subject(root, "Reti")

        self.assertEqual([section.name for section in layout.sections], ["02_Indirizzamento", "10_Routing"])
        self.assertEqual(layout.shared_input_dir, subject / "_condivise")
        self.assertEqual(layout.sections[0].output_dir, root / "output" / "Reti" / "02_Indirizzamento")

    def test_reports_files_directly_in_subject_without_assigning_them(self):
        root = self.fixture("loose_file")
        subject = root / "input" / "Reti"
        loose_file = subject / "fonte_senza_sezione.txt"

        layout = discover_subject(root, "Reti")

        self.assertEqual(len(layout.issues), 1)
        self.assertEqual(layout.issues[0].path, loose_file)

    def test_rejects_missing_or_duplicate_numeric_prefixes(self):
        missing_prefix_root = self.fixture("missing_prefix")
        with self.assertRaisesRegex(PathContractError, "senza prefisso"):
            discover_subject(missing_prefix_root, "Reti")

        duplicate_prefix_root = self.fixture("duplicate_prefix")
        with self.assertRaisesRegex(PathContractError, "duplicato"):
            discover_subject(duplicate_prefix_root, "Reti")

    def test_allows_missing_and_rejects_invalid_subject_configuration(self):
        missing_config_root = self.fixture("missing_config")
        discovered = discover_subject(missing_config_root, "Reti")
        self.assertEqual(discovered.sections, ())

        invalid_config_root = self.fixture("invalid_config")
        with self.assertRaisesRegex(PathContractError, "oggetto JSON"):
            discover_subject(invalid_config_root, "Reti")

    def test_reports_files_at_input_root_and_rejects_path_escape(self):
        root = self.fixture("input_root_file")
        names, issues = discover_subject_names(root)

        self.assertEqual(names, ())
        self.assertEqual(len(issues), 1)
        with self.assertRaises(PathContractError):
            ensure_within(root / "input" / ".." / "outside", root / "input")


if __name__ == "__main__":
    unittest.main()
