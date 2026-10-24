from __future__ import annotations

import json

from appunti_ai.assembly.subject import assemble_subject
from appunti_ai.contracts.paths import discover_subject, ensure_within
from appunti_ai.extraction.scoped import build_scope, extract_scope


def run_preparation(project_root, subject_name: str, *, section_name: str | None = None, extract: bool = True, assemble: bool = False) -> dict[str, object]:
    """Esegue solo fasi deterministiche già supportate e restituisce un report."""
    subject = discover_subject(project_root, subject_name)
    selected = [section for section in subject.sections if section_name is None or section.name == section_name]
    if section_name is not None and not selected:
        raise ValueError(f"Sezione non trovata: {section_name}")
    report: dict[str, object] = {"subject": subject.name, "sections": [section.name for section in selected]}
    if extract:
        extracted: dict[str, object] = {}
        if subject.shared_input_dir is not None:
            try:
                extracted["_condivise"] = extract_scope(build_scope(subject, None, shared=True))
            except Exception as exc:
                extracted["_condivise"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        for section in selected:
            try:
                extracted[section.name] = extract_scope(build_scope(subject, section.name, shared=False))
            except Exception as exc:
                extracted[section.name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        report["extraction"] = extracted
    if assemble:
        report["assembly"] = assemble_subject(subject)
    report_path = ensure_within(subject.output_dir / "pipeline_report.json", subject.output_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    report["report_path"] = str(report_path)
    return report
