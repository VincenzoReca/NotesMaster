import unittest
from pathlib import Path
from scripts import generate_notes, merge_notes, diagnose_model, pipeline_paths, scoped_extraction, test_vision, extract_sources
from appunti_ai.consolidation import notes as consolidation_notes
from appunti_ai.contracts import paths as contract_paths
from appunti_ai.diagnostics import model as diagnostic_model
from appunti_ai.diagnostics import vision_smoke
from appunti_ai.extraction import scoped as scoped_module
from appunti_ai.generation import notes as generation_notes


class EntrypointCompatibilityTests(unittest.TestCase):
    PROJECT_ROOT = Path(__file__).resolve().parents[1]

    def test_root_wrappers_reexport_business_functions(self):
        self.assertIs(pipeline_paths.discover_subject, contract_paths.discover_subject)
        self.assertIs(scoped_extraction.extract_scope, scoped_module.extract_scope)
        self.assertIs(generate_notes.make_prompt, generation_notes.make_prompt)
        self.assertIs(merge_notes.consolidate_chunks, consolidation_notes.consolidate_chunks)
        self.assertIs(diagnose_model.run_case, diagnostic_model.run_case)
        self.assertIs(test_vision.run, vision_smoke.run)
        from appunti_ai.extraction import legacy
        self.assertIs(extract_sources.safe_name, legacy.safe_name)

    def test_moved_modules_keep_the_project_root(self):
        self.assertEqual(generation_notes.ROOT, self.PROJECT_ROOT)
        self.assertEqual(consolidation_notes.ROOT, self.PROJECT_ROOT)
        self.assertEqual(diagnostic_model.ROOT, self.PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
