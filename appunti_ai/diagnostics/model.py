"""Esegue test minimi e riproducibili della chiamata a Ollama.

Non modifica output/draft né il relativo state.json. Ogni risposta viene
salvata in output/diagnostics per permettere di capire dove Ollama colloca
il testo prodotto (content, thinking, o nessuno dei due).
"""

from __future__ import annotations

import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import ollama
from appunti_ai.modeling.profiles import DEFAULT_PROFILE_FILE, ProfileError, load_profile


ROOT = Path(__file__).resolve().parents[2]
EXTRACTED = ROOT / "output" / "extracted"
DIAGNOSTICS = ROOT / "output" / "diagnostics"
MODEL = "qwen3-vl:4b"
NUM_CTX = 4096
NUM_PREDICT = 1200


def to_json_value(value: Any) -> Any:
    """Converte in modo prudente gli oggetti della libreria ollama."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Tipo non serializzabile: {type(value).__name__}")


def page_section(markdown: str, page: int) -> str:
    heading = f"## Pagina {page}"
    start = markdown.find(heading)
    if start == -1:
        raise ValueError(f"Pagina {page} non trovata nel Markdown estratto.")
    next_heading = markdown.find("\n## Pagina ", start + len(heading))
    return markdown[start:] if next_heading == -1 else markdown[start:next_heading]


def run_case(
    name: str,
    text: str,
    image: Path | None,
    think: bool,
    raw_no_think_workaround: bool = False,
) -> None:
    message: dict[str, Any] = {
        "role": "user",
        "content": (
            "Analizza esclusivamente il seguente estratto universitario in italiano. "
            "Restituisci due punti importanti in Markdown; non usare conoscenze "
            "esterne.\n\nESTRATTO:\n" + text
        ),
    }
    if raw_no_think_workaround:
        # La variante qwen3-vl-thinking locale ignora il flag think=False.
        # Chiudiamo esplicitamente il blocco <think> nel formato ChatML.
        message["content"] += "\n/no_think"
        messages = [
            message,
            {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
        ]
    else:
        messages = [message]

    if image is not None:
        message["images"] = [str(image)]

    print(f"Avvio caso '{name}' ({'con immagine' if image else 'solo testo'})...")
    started_at = datetime.now(timezone.utc).isoformat()
    output = DIAGNOSTICS / f"{name}.jsonl"
    header = {
        "record_type": "request",
        "case": name,
        "started_at": started_at,
        "model": MODEL,
        "image": str(image) if image else None,
        "request": message,
        "options": {
            "temperature": 0.2,
            "num_ctx": NUM_CTX,
            "num_predict": NUM_PREDICT,
        },
        "think": think,
        "raw_no_think_workaround": raw_no_think_workaround,
    }
    # Scriviamo il contesto prima della chiamata: anche un arresto del client
    # lascia una traccia diagnostica leggibile.
    output.write_text(json.dumps(header, ensure_ascii=False) + "\n", encoding="utf-8")

    content_length = 0
    thinking_length = 0
    try:
        if raw_no_think_workaround:
            payload = {
                "model": MODEL,
                "messages": messages,
                "stream": True,
                "think": think,
                "raw": True,
                "options": {
                    "temperature": 0.2,
                    "num_ctx": NUM_CTX,
                    "num_predict": NUM_PREDICT,
                },
            }
            request = Request(
                "http://127.0.0.1:11434/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=900) as http_response:
                response_stream = (
                    json.loads(line.decode("utf-8"))
                    for line in http_response
                    if line.strip()
                )
                content_length, thinking_length = write_stream(output, response_stream)
        else:
            response_stream = ollama.chat(
                model=MODEL,
                messages=messages,
                stream=True,
                think=think,
                options={
                    "temperature": 0.2,
                    "num_ctx": NUM_CTX,
                    "num_predict": NUM_PREDICT,
                },
            )
            content_length, thinking_length = write_stream(output, response_stream)
        error = None
    except Exception as exc:  # Il test deve registrare anche errori API.
        error = f"{type(exc).__name__}: {exc}"
        with output.open("a", encoding="utf-8") as stream_file:
            stream_file.write(json.dumps({"record_type": "error", "error": error}) + "\n")

    if error:
        print(f"  ERRORE: {error}")
    else:
        print(f"  Salvato: {output}")
        print(f"  content: {content_length} caratteri")
        print(f"  thinking: {thinking_length} caratteri")


def write_stream(output: Path, response_stream: Any) -> tuple[int, int]:
    """Salva subito ciascun frammento, incluso il ragionamento interno."""
    content_length = 0
    thinking_length = 0
    with output.open("a", encoding="utf-8") as stream_file:
        for response in response_stream:
            response_data = to_json_value(response)
            message_data = response_data.get("message", {})
            content_length += len(message_data.get("content") or "")
            thinking_length += len(message_data.get("thinking") or "")
            stream_file.write(
                json.dumps({"record_type": "chunk", "data": response_data}, ensure_ascii=False)
                + "\n"
            )
            stream_file.flush()
    return content_length, thinking_length


def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=("text", "image", "both", "raw-text"),
        default="both",
        help="Caso diagnostico da eseguire (predefinito: entrambi).",
    )
    parser.add_argument("--model", default=None, help="Override del tag Ollama nel profilo.")
    parser.add_argument("--model-profile", help="Profilo modello da usare.")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILE_FILE)
    args = parser.parse_args()
    try:
        profile = load_profile(args.model_profile, args.profiles)
        if args.case in {"image", "both"}:
            profile.require({"vision"})
        MODEL = args.model or profile.model
    except ProfileError as exc:
        raise SystemExit(f"Profilo modello non valido: {exc}") from exc
    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    markdown = (EXTRACTED / "week-01-course-intro-and-toolchain.md").read_text(
        encoding="utf-8"
    )

    # Il test precedente, con thinking predefinito, non ha prodotto neppure un
    # frammento. Qui lo disattiviamo per verificare l'ipotesi senza alterare la
    # pipeline principale.
    if args.case in ("text", "both"):
        run_case("text_page_2_no_thinking", page_section(markdown, 2), image=None, think=False)
    if args.case == "raw-text":
        run_case(
            "text_page_2_raw_no_think",
            page_section(markdown, 2),
            image=None,
            think=False,
            raw_no_think_workaround=True,
        )
    if args.case in ("image", "both"):
        run_case(
            "image_page_1_no_thinking",
            page_section(markdown, 1),
            image=ROOT / "output" / "images" / "week-01-course-intro-and-toolchain" / "page_0001.jpg",
            think=False,
        )


if __name__ == "__main__":
    main()
