"""Estrazione isolata per una sezione o fonti condivise di una materia.

Il modulo non modifica gli input né rimuove estratti precedenti: ogni
estrazione valida è identificata dall'hash della fonte e il manifest indica
quale versione è corrente per lo scope richiesto.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import fitz
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from appunti_ai.contracts.paths import SubjectPaths, ensure_within
from appunti_ai.extraction.media import MEDIA_EXTENSIONS, unsupported_media_record


EXTRACTION_VERSION = "scoped-extraction-v2"
SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ExtractionError(RuntimeError):
    """Errore di estrazione confinato allo scope richiesto."""


@dataclass(frozen=True)
class ExtractionScope:
    subject: str
    kind: str
    source_root: Path
    output_root: Path
    extracted_dir: Path
    visual_dir: Path
    manifest_path: Path


def _safe_name(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE)
    return value.strip("._") or "source"


def _source_role(path: Path) -> str:
    name = path.stem.lower()
    if "sbobin" in name or "transcript" in name:
        return "primary_lecture"
    if "slide" in name or "presentazione" in name:
        return "professor_slides"
    if "appunti" in name or "notes" in name:
        return "personal_notes"
    return "unclassified"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": EXTRACTION_VERSION, "sources": {}}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Manifest di estrazione non valido: {path}: {exc.msg}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), dict):
        raise ExtractionError(f"Manifest di estrazione incompatibile: {path}")
    if manifest.get("version") != EXTRACTION_VERSION:
        # Version changes invalidate cache reuse but preserve old extracted
        # artifacts until the new manifest has been written successfully.
        return {"version": EXTRACTION_VERSION, "sources": {}}
    return manifest


def build_scope(subject: SubjectPaths, section_name: str | None, shared: bool) -> ExtractionScope:
    """Costruisce uno scope valido senza creare directory o indovinare sezioni."""
    if shared == (section_name is not None):
        raise ExtractionError("Specificare una sezione oppure --shared, ma non entrambi.")

    if shared:
        if subject.shared_input_dir is None or subject.shared_output_dir is None:
            raise ExtractionError(f"La materia {subject.name} non contiene _condivise.")
        source_root = subject.shared_input_dir
        output_root = subject.shared_output_dir
        visual_dirname = "pages"
        kind = "shared"
    else:
        section = next((item for item in subject.sections if item.name == section_name), None)
        if section is None:
            raise ExtractionError(f"Sezione non trovata nella materia {subject.name}: {section_name}")
        source_root = section.input_dir
        output_root = section.output_dir
        visual_dirname = "images"
        kind = "section"

    return ExtractionScope(
        subject=subject.name,
        kind=kind,
        source_root=source_root,
        output_root=output_root,
        extracted_dir=ensure_within(output_root / "extracted", output_root),
        visual_dir=ensure_within(output_root / visual_dirname, output_root),
        manifest_path=ensure_within(output_root / "extraction_manifest.json", output_root),
    )


def _relative_source_path(path: Path, scope: ExtractionScope) -> str:
    try:
        return path.resolve(strict=True).relative_to(scope.source_root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise ExtractionError(f"Fonte fuori dallo scope autorizzato: {path}") from exc


def _source_identifier(relative_path: str, source_hash: str) -> str:
    return f"{_safe_name(relative_path)}__{source_hash[:12]}"


def _save_jpeg(data: bytes, destination: Path) -> bool:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((1400, 1400))
            image.save(destination, "JPEG", quality=78, optimize=True)
        return True
    except Exception:
        return False


def _extract_pdf(path: Path, role: str, identifier: str, scope: ExtractionScope) -> tuple[str, dict[str, object]]:
    page_dir = ensure_within(scope.visual_dir / identifier, scope.visual_dir)
    page_dir.mkdir(parents=True, exist_ok=True)
    sections = [f"# {path.name}", "", f"- Tipo fonte: `{role}`", f"- File originale: `{path.name}`", ""]
    pages: list[dict[str, object]] = []

    with fitz.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            should_render = role == "professor_slides" or len(text) < 80 or bool(page.get_images(full=True))
            visuals: list[str] = []
            if should_render:
                image_path = page_dir / f"page_{page_number:04d}.jpg"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
                pixmap.save(str(image_path), jpg_quality=75)
                visuals.append(image_path.relative_to(scope.output_root).as_posix())

            sections.extend([
                f"## Pagina {page_number}",
                "",
                text if text else "[Nessun testo estraibile: pagina da verificare come scansione o figura]",
                "",
            ])
            for visual in visuals:
                sections.extend([f"Immagine pagina: `../{visual}`", ""])
            pages.append({
                "page": page_number,
                "text_chars": len(text),
                "images": visuals,
                "likely_scanned_or_visual_only": not bool(text),
            })

    return "\n".join(sections), {"type": "pdf", "pages": pages}


def _extract_docx(path: Path, role: str, identifier: str, scope: ExtractionScope) -> tuple[str, dict[str, object]]:
    image_dir = ensure_within(scope.visual_dir / identifier, scope.visual_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    document = Document(path)
    sections = [f"# {path.name}", "", f"- Tipo fonte: `{role}`", f"- File originale: `{path.name}`", ""]
    images: list[dict[str, object]] = []
    image_counter = 0
    paragraph_count = 0

    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if text:
            paragraph_count += 1
            sections.extend([f"## Paragrafo {index}", "", text, ""])
        for blip in paragraph._p.xpath(".//a:blip"):
            relationship_id = blip.get(qn("r:embed"))
            image_part = document.part.related_parts.get(relationship_id) if relationship_id else None
            if image_part is None:
                continue
            image_counter += 1
            destination = image_dir / f"image_{image_counter:04d}.jpg"
            if not _save_jpeg(image_part.blob, destination):
                images.append({"paragraph": index, "error": "image_conversion_failed"})
                continue
            relative_image = destination.relative_to(scope.output_root).as_posix()
            sections.extend([f"**[Immagine {image_counter} nel paragrafo {index}]**", "", f"![Immagine {image_counter}](../{relative_image})", ""])
            images.append({"paragraph": index, "path": relative_image})

    for table_index, table in enumerate(document.tables, start=1):
        sections.extend([f"## Tabella {table_index}", ""])
        for row in table.rows:
            sections.append(" | ".join(cell.text.replace("\n", " / ").strip() for cell in row.cells))
        sections.append("")

    return "\n".join(sections), {"type": "docx", "paragraphs": paragraph_count, "images": images, "tables": len(document.tables)}


def _extract_text(path: Path, role: str) -> tuple[str, dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1252")
    if not text.strip():
        raise ExtractionError("Il file è vuoto.")
    content = "\n".join([
        f"# {path.name}", "", f"- Tipo fonte: `{role}`", f"- File originale: `{path.name}`", "", "---", "", text.strip(), "",
    ])
    return content, {"type": path.suffix.lower().lstrip("."), "text_chars": len(text)}


def _extract_one(path: Path, role: str, identifier: str, scope: ExtractionScope) -> tuple[str, dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path, role, identifier, scope)
    if suffix == ".docx":
        return _extract_docx(path, role, identifier, scope)
    if suffix in IMAGE_EXTENSIONS:
        image_target_dir = ensure_within(scope.visual_dir / identifier, scope.visual_dir)
        image_target_dir.mkdir(parents=True, exist_ok=True)
        image_target = ensure_within(image_target_dir / path.name, image_target_dir)
        shutil.copy2(path, image_target)
        # Extracted Markdown lives in ``extracted/``; image links are relative
        # to that directory, while the actual asset remains under ``images/``.
        relative_image = "../" + image_target.relative_to(scope.output_root).as_posix()
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Exception as exc:
            raise ExtractionError(f"Immagine non leggibile: {exc}") from exc
        content = "\n".join([
            f"# {path.name}", "", f"- Tipo fonte: `{role}`", f"- File originale: `{path.name}`", "",
            f"![Source image]({relative_image})", "",
        ])
        return content, {"type": "image", "width": width, "height": height, "image": relative_image}
    return _extract_text(path, role)


def extract_scope(scope: ExtractionScope) -> dict[str, object]:
    """Estrae tutte le fonti dello scope esplicito e salva solo nuovi risultati.

    Le fonti vengono cercate ricorsivamente soltanto sotto ``scope.source_root``;
    ogni file risolto è nuovamente verificato contro quella radice.
    """
    if not scope.source_root.is_dir():
        raise ExtractionError(f"Directory fonti mancante: {scope.source_root}")
    scope.extracted_dir.mkdir(parents=True, exist_ok=True)
    scope.visual_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(scope.manifest_path)
    prior_sources = manifest["sources"]
    assert isinstance(prior_sources, dict)
    current_sources: dict[str, object] = {}

    for source_path in sorted((item for item in scope.source_root.rglob("*") if item.is_file()), key=lambda item: item.as_posix().casefold()):
        relative_path = _relative_source_path(source_path, scope)
        source_hash = _sha256(source_path)
        suffix = source_path.suffix.lower()
        previous = prior_sources.get(relative_path)
        if suffix not in SUPPORTED_EXTENSIONS:
            if suffix in MEDIA_EXTENSIONS:
                current_sources[relative_path] = unsupported_media_record(source_path, source_hash)
            else:
                current_sources[relative_path] = {"sha256": source_hash, "status": "unsupported", "extension": suffix, "diagnostic": "Estensione non supportata dall'estrattore."}
            continue

        if isinstance(previous, dict) and previous.get("sha256") == source_hash and previous.get("status") == "completed":
            previous_output = previous.get("extracted_path")
            if isinstance(previous_output, str) and ensure_within(scope.output_root / previous_output, scope.output_root).is_file():
                current_sources[relative_path] = previous
                continue

        role = _source_role(source_path)
        identifier = _source_identifier(relative_path, source_hash)
        extracted_path = ensure_within(scope.extracted_dir / f"{identifier}.md", scope.extracted_dir)
        try:
            extracted_text, details = _extract_one(source_path, role, identifier, scope)
            _atomic_write_text(extracted_path, extracted_text)
            current_sources[relative_path] = {
                "sha256": source_hash,
                "status": "completed",
                "role": role,
                "extracted_path": extracted_path.relative_to(scope.output_root).as_posix(),
                "details": details,
            }
        except Exception as exc:
            current_sources[relative_path] = {
                "sha256": source_hash,
                "status": "failed",
                "role": role,
                "error": f"{type(exc).__name__}: {exc}",
            }

    result = {
        "version": EXTRACTION_VERSION,
        "subject": scope.subject,
        "scope_kind": scope.kind,
        "source_root": str(scope.source_root),
        "sources": current_sources,
    }
    _atomic_write_text(scope.manifest_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    audit_dir = ensure_within(scope.output_root / "audit", scope.output_root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    summary = {"completed": [], "unsupported": [], "unsupported_media": [], "failed": []}
    for source_name, record in current_sources.items():
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        if status in summary:
            summary[status].append(source_name)
    audit = {"version": 1, "subject": scope.subject, "scope_kind": scope.kind, "manifest": str(scope.manifest_path), "summary": summary}
    audit_path = ensure_within(audit_dir / "extraction_audit.json", audit_dir)
    _atomic_write_text(audit_path, json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    return result
