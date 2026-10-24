from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from appunti_ai.contracts.paths import SubjectPaths, ensure_within


class AssemblyError(RuntimeError):
    pass


def _valid_final(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if len(text) < 40 or text.startswith("<!--"):
        return False
    forbidden = ("[da verificare]", "[truncated]", "[output truncated]", "<think>")
    return not any(marker in text.casefold() for marker in forbidden)


def assemble_subject(subject: SubjectPaths, *, output_name: str = "appunti_materia.md") -> dict[str, object]:
    """Assembla sezioni in ordine numerico senza riscrittura del modello."""
    sections: list[tuple[str, Path, str]] = []
    missing: list[str] = []
    for section in subject.sections:
        final_path = ensure_within(section.output_dir / "appunti_finali.md", section.output_dir)
        if not _valid_final(final_path):
            missing.append(section.name)
            continue
        sections.append((section.name, final_path, final_path.read_text(encoding="utf-8")))
    if missing:
        raise AssemblyError("Assemblaggio bloccato: sezioni mancanti o non valide: " + ", ".join(missing))
    if not sections:
        raise AssemblyError("Nessuna sezione valida da assemblare.")

    pieces = [f"# {subject.name}", "", "> Documento assemblato deterministicamente dalle sezioni validate.", ""]
    records = []
    for name, path, text in sections:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Section finals are authored relative to their section directory.
        # Prefix local image links so they remain valid from the subject file.
        assembled_text = re.sub(r"\]\(images/", f"]({name}/images/", text.strip())
        pieces.extend([f"## {name}", "", assembled_text, ""])
        records.append({"section": name, "path": str(path), "sha256": digest})
    content = "\n".join(pieces).rstrip() + "\n"
    output = ensure_within(subject.output_dir / output_name, subject.output_dir)
    state_path = ensure_within(subject.output_dir / "materia_state.json", subject.output_dir)
    fingerprint = hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    prior = None
    if state_path.is_file():
        try:
            prior = json.loads(state_path.read_text(encoding="utf-8")).get("fingerprint")
        except (OSError, json.JSONDecodeError):
            raise AssemblyError(f"Stato materia non valido: {state_path}")
    if prior != fingerprint or not output.is_file():
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(output)
    state = {"version": 1, "subject": subject.name, "fingerprint": fingerprint, "sections": records, "output": str(output)}
    temporary_state = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary_state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_state.replace(state_path)
    audit_dir = ensure_within(subject.output_dir / "audit", subject.output_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = ensure_within(audit_dir / "assembly_audit.json", audit_dir)
    temporary_audit = audit_path.with_suffix(audit_path.suffix + ".tmp")
    temporary_audit.write_text(json.dumps({"version": 1, "subject": subject.name, "output": str(output), "fingerprint": fingerprint, "sections": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_audit.replace(audit_path)
    return state
