"""Contratti per fonti audio/video senza imporre decoder o trascrittori."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MEDIA_EXTENSIONS = {
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".avi": "video", ".webm": "video",
}


@dataclass(frozen=True)
class MediaSource:
    path: Path
    kind: str
    sha256: str


def media_kind(path: Path) -> str | None:
    return MEDIA_EXTENSIONS.get(path.suffix.casefold())


def unsupported_media_record(path: Path, source_hash: str) -> dict[str, object]:
    kind = media_kind(path)
    return {
        "status": "unsupported_media",
        "media_kind": kind,
        "sha256": source_hash,
        "extension": path.suffix.lower(),
        "provenance": {"source": path.name, "time_ranges": []},
        "diagnostic": "Nessun decoder/trascrittore configurato: la fonte è conservata e richiede una capability media.",
    }
