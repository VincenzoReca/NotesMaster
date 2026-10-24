import shutil
import unittest
from unittest.mock import patch
from pathlib import Path

from appunti_ai.orchestration.pipeline import run_preparation


class OrchestrationTests(unittest.TestCase):
    def test_preparation_reports_discovered_sections_without_model(self):
        root = Path(__file__).parent / ".tmp_orchestration"
        shutil.rmtree(root, ignore_errors=True)
        try:
            subject = root / "input" / "Reti"
            (subject / "01_Uno").mkdir(parents=True)
            (subject / "02_Due").mkdir()
            (subject / "materia.json").write_text("{}", encoding="utf-8")
            report = run_preparation(root, "Reti", extract=False)
            self.assertEqual(report["sections"], ["01_Uno", "02_Due"])
            self.assertNotIn("extraction", report)
            self.assertTrue(Path(report["report_path"]).is_file())
            selected = run_preparation(root, "Reti", section_name="02_Due", extract=False)
            self.assertEqual(selected["sections"], ["02_Due"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_one_section_failure_does_not_stop_other_sections(self):
        root = Path(__file__).parent / ".tmp_orchestration_failure"
        shutil.rmtree(root, ignore_errors=True)
        try:
            subject = root / "input" / "Reti"
            (subject / "01_Uno").mkdir(parents=True)
            (subject / "02_Due").mkdir()
            (subject / "materia.json").write_text("{}", encoding="utf-8")
            def fake_extract(scope):
                if scope.source_root.name == "01_Uno":
                    raise RuntimeError("fixture failure")
                return {"status": "completed"}
            with patch("appunti_ai.orchestration.pipeline.extract_scope", side_effect=fake_extract):
                report = run_preparation(root, "Reti")
            self.assertEqual(report["extraction"]["01_Uno"]["status"], "failed")
            self.assertEqual(report["extraction"]["02_Due"]["status"], "completed")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_dry_run_command_generation_is_side_effect_free(self):
        root = Path(__file__).parent / ".tmp_orchestration_dry"
        shutil.rmtree(root, ignore_errors=True)
        try:
            subject = root / "input" / "Reti"
            (subject / "01_Uno").mkdir(parents=True)
            (subject / "materia.json").write_text("{}", encoding="utf-8")
            report = run_preparation(root, "Reti", extract=False)
            self.assertEqual(report["sections"], ["01_Uno"])
        finally:
            shutil.rmtree(root, ignore_errors=True)
