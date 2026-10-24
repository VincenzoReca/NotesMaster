"""Smoke test esplicito per la visione locale tramite Ollama.

Non viene eseguito all'importazione: richiede l'avvio esplicito come script.
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "qwen3-vl:4b-instruct"
DEFAULT_IMAGE = PROJECT_ROOT / "test.png"


def run(model: str, image: Path, timeout: int) -> None:
    if not image.is_file():
        raise FileNotFoundError(f"Immagine non trovata: {image.resolve()}")

    image_base64 = base64.b64encode(image.read_bytes()).decode("utf-8")
    response = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Descrivi in italiano ciò che vedi nell'immagine "
                        "in 3 frasi. Non limitarti a ragionare: "
                        "fornisci una risposta finale."
                    ),
                    "images": [image_base64],
                }
            ],
            "stream": False,
            "think": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    message = data.get("message", {})
    content = message.get("content", "")
    thinking = message.get("thinking", "")

    print("\n--- RISPOSTA DEL MODELLO ---")
    print(content or "[NESSUN TESTO NEL CAMPO content]")
    print("\n--- DIAGNOSTICA ---")
    print("Modello:", data.get("model"))
    print("Caratteri content:", len(content))
    print("Caratteri thinking:", len(thinking))
    print("Done reason:", data.get("done_reason"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Esegue un singolo smoke test visivo su Ollama.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    run(args.model, args.image, args.timeout)


if __name__ == "__main__":
    main()
