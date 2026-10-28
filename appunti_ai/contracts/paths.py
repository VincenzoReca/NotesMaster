"""Contratti deterministici per materie, sezioni e percorsi dell'applicazione.

Questo modulo non crea directory e non legge fonti didattiche. Fornisce alla
pipeline una sola interpretazione verificabile della struttura ``input/`` e
dei corrispondenti percorsi in ``output/``.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_INPUT_DIRNAME = "input"
DEFAULT_OUTPUT_DIRNAME = "output"
SUBJECT_CONFIG_FILENAME = "materia.json"
SUBJECT_CONFIG_VERSION = 1
SHARED_SOURCES_DIRNAME = "_condivise"
SECTION_PREFIX = re.compile(r"^(\d+)_")


class PathContractError(ValueError):
    """La struttura o un percorso non rispettano il contratto della pipeline."""


@dataclass(frozen=True)
class DiscoveryIssue:
    """Anomalia da mostrare all'utente, senza assegnare file per supposizione."""

    path: Path
    message: str


@dataclass(frozen=True)
class SectionPaths:
    name: str
    input_dir: Path
    output_dir: Path
    order: int


@dataclass(frozen=True)
class SubjectPaths:
    name: str
    project_root: Path
    input_dir: Path
    output_dir: Path
    config_path: Path
    shared_input_dir: Path | None
    shared_output_dir: Path | None
    sections: tuple[SectionPaths, ...]
    issues: tuple[DiscoveryIssue, ...]


DEFAULT_SUBJECT_CONFIG: dict[str, object] = {
    "version": SUBJECT_CONFIG_VERSION,
    "sections_discovery": "immediate_subfolders",
    "shared_sources_dir": SHARED_SOURCES_DIRNAME,
    "shared_sources_enabled": True,
    "final_filename": "appunti_finali.md",
    "subject_filename": "appunti_materia.md",
    "merge_mode": "deterministic",
}


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def ensure_within(path: Path, root: Path) -> Path:
    """Restituisce il percorso risolto oppure rifiuta traversal fuori da root."""
    resolved_root = _resolved(root)
    resolved_path = _resolved(path)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise PathContractError(
            f"Percorso fuori dalla directory consentita: {resolved_path} "
            f"(radice: {resolved_root})"
        ) from exc
    return resolved_path


def _validate_directory_name(name: str, label: str) -> None:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise PathContractError(f"Nome {label} non valido: {name!r}")


def project_paths(project_root: Path) -> tuple[Path, Path, Path]:
    """Risolve le tre radici senza crearle."""
    root = _resolved(project_root)
    return root, root / DEFAULT_INPUT_DIRNAME, root / DEFAULT_OUTPUT_DIRNAME


def load_subject_config(config_path: Path) -> dict[str, object]:
    """Valida soltanto il contratto minimo, senza introdurre opzioni attive."""
    if not config_path.is_file():
        raise PathContractError(f"Configurazione materia mancante: {config_path}")
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise PathContractError(f"Configurazione non UTF-8: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise PathContractError(f"JSON non valido in {config_path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise PathContractError(f"La configurazione deve essere un oggetto JSON: {config_path}")
    optional_types = {"name": str, "shared_sources_dir": str, "sections_discovery": str, "shared_sources_enabled": bool, "final_filename": str, "subject_filename": str, "merge_mode": str}
    for key, expected in optional_types.items():
        if key in value and not isinstance(value[key], expected):
            raise PathContractError(f"Campo materia.json non valido {key!r}: atteso {expected.__name__}")
    return normalize_subject_config(value)


def normalize_subject_config(config: dict[str, object]) -> dict[str, object]:
    """Applica default v1 senza mutare il dizionario originale."""
    normalized = dict(DEFAULT_SUBJECT_CONFIG)
    normalized.update(config)
    if normalized.get("version") != SUBJECT_CONFIG_VERSION:
        raise PathContractError(f"Versione materia.json non supportata: {normalized.get('version')!r}")
    if normalized["sections_discovery"] != "immediate_subfolders":
        raise PathContractError("sections_discovery deve essere 'immediate_subfolders'")
    if normalized["merge_mode"] != "deterministic":
        raise PathContractError("merge_mode deve essere 'deterministic'")
    for key in ("shared_sources_dir", "final_filename", "subject_filename"):
        value = normalized[key]
        if not isinstance(value, str) or not value or Path(value).name != value:
            raise PathContractError(f"Valore non valido in materia.json: {key}")
    return normalized


def _section_order(name: str) -> int:
    match = SECTION_PREFIX.match(name)
    if not match:
        raise PathContractError(
            f"Sezione senza prefisso numerico non ambiguo: {name}. "
            "Usare ad esempio '01_Introduzione'."
        )
    return int(match.group(1))


def discover_subject(project_root: Path, subject_name: str) -> SubjectPaths:
    """Scopre una sola materia e le sue sole sezioni immediate.

    La funzione è deliberatamente in sola lettura: non crea output e non
    interpreta file collocati direttamente nella materia come fonti di una
    sezione.
    """
    _validate_directory_name(subject_name, "materia")
    root, input_root, output_root = project_paths(project_root)
    subject_input = ensure_within(input_root / subject_name, input_root)
    subject_output = ensure_within(output_root / subject_name, output_root)
    if not subject_input.is_dir():
        raise PathContractError(f"Materia non trovata: {subject_input}")

    config_path = ensure_within(subject_input / SUBJECT_CONFIG_FILENAME, subject_input)
    # Configuration is optional for ordinary runs. The directory contract and
    # safe defaults are sufficient; materia.json remains an optional override.
    if config_path.is_file():
        load_subject_config(config_path)

    issues: list[DiscoveryIssue] = []
    section_dirs: list[Path] = []
    shared_input: Path | None = None

    for child in sorted(subject_input.iterdir(), key=lambda item: item.name.casefold()):
        if child.name == SUBJECT_CONFIG_FILENAME:
            continue
        if child.name == SHARED_SOURCES_DIRNAME:
            if not child.is_dir():
                raise PathContractError(f"{SHARED_SOURCES_DIRNAME} deve essere una directory: {child}")
            shared_input = ensure_within(child, subject_input)
            continue
        if child.is_dir():
            section_dirs.append(ensure_within(child, subject_input))
        else:
            issues.append(
                DiscoveryIssue(
                    path=ensure_within(child, subject_input),
                    message="File direttamente nella materia: non assegnato a una sezione.",
                )
            )

    numbered_sections = [(directory, _section_order(directory.name)) for directory in section_dirs]
    seen_orders: dict[int, Path] = {}
    for directory, order in numbered_sections:
        previous = seen_orders.get(order)
        if previous is not None:
            raise PathContractError(
                f"Prefisso sezione duplicato {order:02d}: {previous.name} e {directory.name}"
            )
        seen_orders[order] = directory

    sections = tuple(
        SectionPaths(
            name=directory.name,
            input_dir=directory,
            output_dir=ensure_within(subject_output / directory.name, subject_output),
            order=order,
        )
        for directory, order in sorted(numbered_sections, key=lambda item: (item[1], item[0].name.casefold()))
    )
    shared_output = (
        ensure_within(subject_output / SHARED_SOURCES_DIRNAME, subject_output)
        if shared_input is not None
        else None
    )

    return SubjectPaths(
        name=subject_name,
        project_root=root,
        input_dir=subject_input,
        output_dir=subject_output,
        config_path=config_path,
        shared_input_dir=shared_input,
        shared_output_dir=shared_output,
        sections=sections,
        issues=tuple(issues),
    )


def discover_subject_names(project_root: Path) -> tuple[tuple[str, ...], tuple[DiscoveryIssue, ...]]:
    """Elenca candidate materie immediate e segnala file fuori struttura."""
    _, input_root, _ = project_paths(project_root)
    if not input_root.is_dir():
        raise PathContractError(f"Directory input mancante: {input_root}")

    names: list[str] = []
    issues: list[DiscoveryIssue] = []
    for child in sorted(input_root.iterdir(), key=lambda item: item.name.casefold()):
        if child.is_dir():
            names.append(child.name)
        else:
            issues.append(
                DiscoveryIssue(
                    path=ensure_within(child, input_root),
                    message="File direttamente in input: non assegnato a una materia.",
                )
            )
    return tuple(names), tuple(issues)


def _format_issues(issues: Iterable[DiscoveryIssue]) -> list[str]:
    return [f"- {issue.path}: {issue.message}" for issue in issues]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica la struttura di input di NotesMaster.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--subject", help="Materia da verificare; se omesso elenca le candidate.")
    args = parser.parse_args()

    try:
        if args.subject:
            subject = discover_subject(args.project_root, args.subject)
            print(f"Materia valida: {subject.name}")
            print(f"Sezioni ({len(subject.sections)}):")
            for section in subject.sections:
                print(f"  {section.order:02d}  {section.name}")
            if subject.shared_input_dir:
                print(f"Fonti condivise: {subject.shared_input_dir}")
            issues = subject.issues
        else:
            names, issues = discover_subject_names(args.project_root)
            print(f"Materie candidate ({len(names)}):")
            for name in names:
                print(f"  {name}")
        if issues:
            print("Anomalie da risolvere:")
            print("\n".join(_format_issues(issues)))
            return 2
        return 0
    except PathContractError as exc:
        print(f"ERRORE: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
