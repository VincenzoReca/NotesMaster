
import base64
import json
from pathlib import Path

import requests

MODEL = "qwen3-vl:4b-instruct"
IMAGE = Path("test.png")

if not IMAGE.exists():
    raise FileNotFoundError(f"Immagine non trovata: {IMAGE.resolve()}")

image_base64 = base64.b64encode(IMAGE.read_bytes()).decode("utf-8")

response = requests.post(
    "http://localhost:11434/api/chat",
    json={
        "model": MODEL,
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
    timeout=180,
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
