import json
import shutil
import unittest
from pathlib import Path

from appunti_ai.assembly.subject import AssemblyError, assemble_subject
from appunti_ai.contracts.paths import discover_subject


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / ".tmp_assembly"
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / "input" / "Reti" / "01_Uno").mkdir(parents=True)
        (self.root / "input" / "Reti" / "02_Due").mkdir()
        (self.root / "input" / "Reti" / "materia.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_assembles_in_order_and_records_hashes(self):
        out = self.root / "output" / "Reti"
        (out / "01_Uno").mkdir(parents=True)
        (out / "02_Due").mkdir(parents=True)
        for path, text in ((out / "01_Uno" / "appunti_finali.md", "Contenuto della prima sezione sufficientemente lungo."), (out / "02_Due" / "appunti_finali.md", "Contenuto della seconda sezione sufficientemente lungo.")):
            path.write_text(text, encoding="utf-8")
        state = assemble_subject(discover_subject(self.root, "Reti"))
        content = (out / "appunti_materia.md").read_text(encoding="utf-8")
        self.assertLess(content.index("01_Uno"), content.index("02_Due"))
        self.assertEqual(len(state["sections"]), 2)
        self.assertTrue((out / "audit" / "assembly_audit.json").is_file())

    def test_rewrites_section_image_links_for_subject_document(self):
        out = self.root / "output" / "Reti"
        (out / "01_Uno").mkdir(parents=True)
        (out / "02_Due").mkdir(parents=True)
        (out / "01_Uno" / "appunti_finali.md").write_text("# Prima\n\nTesto sufficientemente lungo per la validazione del documento.\n\n![Figure](images/diagram.jpg)", encoding="utf-8")
        (out / "02_Due" / "appunti_finali.md").write_text("# Seconda\n\nTesto sufficientemente lungo per la validazione del documento.", encoding="utf-8")
        assemble_subject(discover_subject(self.root, "Reti"))
        content = (out / "appunti_materia.md").read_text(encoding="utf-8")
        self.assertIn("01_Uno/images/diagram.jpg", content)

    def test_missing_section_blocks(self):
        with self.assertRaises(AssemblyError):
            assemble_subject(discover_subject(self.root, "Reti"))

    def test_process_placeholders_block_final_assembly(self):
        out = self.root / "output" / "Reti" / "01_Uno"
        out.mkdir(parents=True)
        (out / "appunti_finali.md").write_text("Contenuto valido ma [DA VERIFICARE] come placeholder.", encoding="utf-8")
        with self.assertRaises(AssemblyError):
            assemble_subject(discover_subject(self.root, "Reti"))
