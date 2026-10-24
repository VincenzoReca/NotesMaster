"""Retrieval lessicale locale, senza dipendenze esterne.

L'indice è un acceleratore: i Markdown estratti restano la fonte canonica.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

INDEX_VERSION = "lexical-index-v1"
TOKEN_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿ]{3,}", re.UNICODE)


class RetrievalError(RuntimeError):
    pass


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in TOKEN_RE.finditer(text)}


def _digest_manifest(manifest_path: Path) -> str:
    try:
        return hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RetrievalError(f"Manifest condiviso non leggibile: {manifest_path}") from exc


def build_index(shared_output: Path, *, max_chunk_chars: int = 6000) -> dict[str, object]:
    """Crea o riusa ``index/lexical.json`` per un archivio condiviso."""
    manifest_path = shared_output / "extraction_manifest.json"
    if not manifest_path.is_file():
        raise RetrievalError(f"Manifest condiviso mancante: {manifest_path}")
    extracted = shared_output / "extracted"
    if not extracted.is_dir():
        raise RetrievalError(f"Estratti condivisi mancanti: {extracted}")
    index_path = shared_output / "index" / "lexical.json"
    fingerprint = _digest_manifest(manifest_path)
    if index_path.is_file():
        try:
            cached = json.loads(index_path.read_text(encoding="utf-8"))
            if cached.get("version") == INDEX_VERSION and cached.get("manifest_sha256") == fingerprint:
                return cached
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: list[dict[str, object]] = []
    for source_name, source in sorted((manifest.get("sources") or {}).items()):
        if not isinstance(source, dict) or source.get("status") != "completed":
            continue
        relative = source.get("extracted_path")
        if not isinstance(relative, str):
            continue
        path = (shared_output / relative).resolve()
        try:
            path.relative_to(shared_output.resolve())
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        for number, paragraph in enumerate(paragraphs, start=1):
            for offset in range(0, len(paragraph), max_chunk_chars):
                chunk = paragraph[offset : offset + max_chunk_chars].strip()
                if chunk:
                    entries.append({"source": source_name, "extracted_path": relative, "chunk": number, "text": chunk, "terms": sorted(_tokens(chunk))})
    result = {"version": INDEX_VERSION, "manifest_sha256": fingerprint, "entries": entries}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(index_path)
    return result


def retrieve(index: dict[str, object], query: str, *, limit: int = 5) -> list[dict[str, object]]:
    """Restituisce al massimo ``limit`` passaggi ordinati per punteggio stabile."""
    if limit < 1:
        return []
    query_terms = _tokens(query)
    if not query_terms:
        return []
    scored: list[tuple[int, int, dict[str, object]]] = []
    for position, entry in enumerate(index.get("entries", [])):
        if not isinstance(entry, dict):
            continue
        terms = set(entry.get("terms", []))
        score = len(query_terms & terms)
        if score:
            scored.append((score, position, entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _, _, entry in scored[:limit]]
