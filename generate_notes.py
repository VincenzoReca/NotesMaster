
from pathlib import Path
import argparse
import json
import re
import time

import ollama

ROOT = Path(__file__).resolve().parent
EXTRACTED = ROOT / "output" / "extracted"
DRAFT = ROOT / "output" / "draft"

MODEL = "qwen3-vl:4b-instruct"
MAX_CHARS = 3000
NUM_CTX = 4096
NUM_PREDICT = 2500

IMAGE_PATTERN = re.compile(
    r"!\[[^\]]*\]\(([^)]+)\)"
    r"|Immagine pagina:\s*`([^`]+)`"
)

SECTION_PATTERN = re.compile(
    r"(?=^## (?:Pagina|Paragrafo|Tabella) \d+\s*$)",
    re.MULTILINE,
)


class GenerationValidationError(RuntimeError):
    pass


def has_useful_content(text):
    if not isinstance(text, str):
        return False
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return len(text) >= 20 and bool(re.search(r"[\wÀ-ÿ]", text))


def is_valid_output(path):
    try:
        return path.is_file() and has_useful_content(path.read_text(encoding="utf-8"))
    except OSError:
        return False


def atomic_write_text(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def get_role(text, filename):
    match = re.search(
        r"Tipo fonte:\s*`([^`]+)`", text, re.IGNORECASE
    )
    if match and match.group(1).lower() != "unclassified":
        return match.group(1)

    name = filename.lower()
    if "sbobin" in name or "transcript" in name:
        return "primary_lecture"
    if "slide" in name or "presentazione" in name:
        return "professor_slides"
    if "appunti" in name or "notes" in name:
        return "personal_notes"
    return "unclassified"


def get_sections(text):
    # Caso 1: Markdown strutturato generato da PDF e DOCX.
    parts = SECTION_PATTERN.split(text)
    sections = [
        part.strip()
        for part in parts
        if part.strip().startswith("## ")
    ]

    if sections:
        return sections

    # Caso 2: Markdown o TXT nativo, senza intestazioni
    # del tipo "## Pagina N" o "## Paragrafo N".
    # Rimuove l'intestazione aggiunta dall'estrattore.
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1]

    # Divide il testo in blocchi separati da righe vuote.
    paragraphs = [
        part.strip()
        for part in re.split(r"\n\s*\n", text)
        if part.strip()
    ]

    # Evita di inviare al modello un documento vuoto.
    if not paragraphs:
        return []

    return paragraphs


def extract_images(section, markdown_file):
    paths = []

    for match in IMAGE_PATTERN.finditer(section):
        raw_path = match.group(1) or match.group(2)

        if raw_path.startswith(("http://", "https://")):
            continue

        path = (markdown_file.parent / raw_path).resolve()

        if path.is_file() and path.suffix.lower() in {
            ".jpg", ".jpeg", ".png", ".webp"
        }:
            if path not in paths:
                paths.append(path)

    return paths


def make_tasks(text, markdown_file):
    sections = get_sections(text)
    tasks = []
    text_group = []

    def flush_text_group():
        if text_group:
            tasks.append({
                "text": "\n\n".join(text_group),
                "image": None,
            })
            text_group.clear()

    for section in sections:
        images = extract_images(section, markdown_file)

        if images:
            flush_text_group()

            # Una sola immagine per richiesta, per contenere
            # il consumo di memoria.
            for image_path in images:
                tasks.append({
                    "text": section,
                    "image": image_path,
                })
            continue

        if text_group and sum(map(len, text_group)) + len(section) > MAX_CHARS:
            flush_text_group()

        text_group.append(section)

    flush_text_group()
    return tasks


def make_prompt(role, filename, chunk_number, text, has_image):
    if role == "primary_lecture":
        source_policy = """
Fonte: sbobina della lezione.
Estrai spiegazioni, definizioni, esempi e passaggi logici.
Mantieni i dettagli tecnici importanti e segnala i passaggi ambigui.
Non inventare parti mancanti.
"""
    elif role == "professor_slides":
        source_policy = """
Fonte: slide del professore.
Preserva terminologia, definizioni, formule e relazioni rappresentate.
Se c'è un'immagine, spiega gli elementi visibili e i loro collegamenti.
Non inventare testo illeggibile o dettagli non supportati.
Distingui ciò che è visibile da ciò che puoi soltanto inferire.
"""
    elif role == "personal_notes":
        source_policy = """
Fonte: appunti personali dello studente, potenzialmente inesatti.
Estrai idee, dubbi, esempi e affermazioni da controllare.
NON presentare queste affermazioni come fatti verificati.
Contrassegna i punti che richiedono conferma con [DA VERIFICARE].
Non correggerli usando conoscenze esterne.
"""
    else:
        source_policy = """
Fonte non classificata.
Riporta fedelmente i contenuti e segnala le ambiguità.
Non attribuire alla fonte un'affidabilità che non conosci.
"""

    visual_policy = (
        "È allegata un'immagine: analizzala insieme al testo, "
        "descrivendo solo elementi visibili e relazioni supportate."
        if has_image
        else "Non è allegata un'immagine a questa richiesta."
    )

    return f"""
Stai analizzando una singola porzione di materiale universitario.

File: {filename}
Fonte: {role}
Porzione: {chunk_number}

REGOLE DELLA FONTE:
{source_policy}

REGOLE VISIVE:
{visual_policy}

ISTRUZIONI:
1. Scrivi in italiano, con precisione tecnica.
2. Conserva definizioni, formule, esempi e dettagli importanti.
3. Organizza il risultato con titoli Markdown.
4. Non aggiungere nozioni esterne alla fonte.
5. Se un testo o una figura non sono chiari, dichiaralo.
6. Non fondere questa fonte con altre fonti non ancora analizzate.
7. Non trasformare ipotesi in fatti.

TESTO DELLA FONTE:
{text}

Produci un'analisi strutturata, non un riassunto superficiale.
""".strip()


def process_task(role, filename, number, task):
    prompt = make_prompt(
        role=role,
        filename=filename,
        chunk_number=number,
        text=task["text"],
        has_image=task["image"] is not None,
    )

    message = {
        "role": "user",
        "content": prompt,
    }

    if task["image"] is not None:
        message["images"] = [str(task["image"])]

    response = ollama.chat(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Sei un assistente universitario rigoroso. "
                    "Rispetta la provenienza delle informazioni. "
                    "Non inventare contenuti."
                ),
            },
            message,
        ],
        options={
            "temperature": 0.2,
            "num_ctx": NUM_CTX,
            "num_predict": NUM_PREDICT,
        },
    )

    content = getattr(response.message, "content", None)
    if not has_useful_content(content):
        thinking = getattr(response.message, "thinking", None)
        raise GenerationValidationError(
            f"Risposta senza contenuto utile (thinking_chars={len(thinking or '')})."
        )
    if getattr(response, "done_reason", None) == "length":
        raise GenerationValidationError("Risposta troncata (done_reason='length').")
    return content


def main():
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--source", help="Markdown estratto da elaborare.")
    parser.add_argument("--max-tasks", type=int, help="Limite porzioni per fonte.")
    parser.add_argument("--force", action="store_true", help="Rigenera anche chunk gia' validi.")
    args = parser.parse_args()
    MODEL = args.model
    if not EXTRACTED.exists():
        raise SystemExit(f"Cartella non trovata: {EXTRACTED}")

    files = sorted(EXTRACTED.glob("*.md"))
    if args.source:
        requested = args.source if args.source.endswith(".md") else args.source + ".md"
        files = [file for file in files if file.name == requested]
        if not files:
            raise SystemExit(f"Fonte non trovata: {requested}")
    if not files:
        raise SystemExit("Nessun Markdown estratto trovato.")

    # Verifica il modello prima di elaborare i documenti.
    try:
        available = ollama.list()
        model_names = {
            item.model for item in available.models
        }
        if not any(name.split(":")[0] == MODEL.split(":")[0]
                   for name in model_names):
            raise SystemExit(
                f"Modello {MODEL} non trovato. "
                "Controlla `ollama list`."
            )
    except ollama.ResponseError as exc:
        raise SystemExit(
            f"Ollama non disponibile o errore API: {exc}"
        )

    state_file = DRAFT / "state.json"
    DRAFT.mkdir(parents=True, exist_ok=True)

    state = {}
    if state_file.exists():
        try:
            state = json.loads(
                state_file.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            print("State JSON non valido: verrà ricostruito.")

    for source_file in files:
        source_name = source_file.stem
        source_dir = DRAFT / source_name
        source_dir.mkdir(parents=True, exist_ok=True)

        text = source_file.read_text(encoding="utf-8")
        role = get_role(text, source_file.name)
        tasks = make_tasks(text, source_file)

        print(
            f"\nFonte: {source_file.name} | "
            f"ruolo: {role} | porzioni: {len(tasks)}"
        )

        state.setdefault(source_name, {
            "file": source_file.name,
            "role": role,
            "total": len(tasks),
            "completed": [],
            "errors": {},
        })

        state[source_name]["total"] = len(tasks)

        for index, task in enumerate(tasks, start=1):
            if args.max_tasks is not None and index > args.max_tasks:
                break
            output_file = source_dir / f"chunk_{index:04d}.md"
            key = str(index)

            # Un risultato già salvato viene riutilizzato.
            if not args.force and is_valid_output(output_file):
                if key not in state[source_name]["completed"]:
                    state[source_name]["completed"].append(key)
                continue
            if key in state[source_name]["completed"]:
                state[source_name]["completed"].remove(key)

            print(
                f"  [{index}/{len(tasks)}] "
                f"{'con immagine' if task['image'] else 'solo testo'}"
            )

            try:
                result = process_task(
                    role=role,
                    filename=source_file.name,
                    number=index,
                    task=task,
                )

                metadata = [
                    f"<!-- source: {source_file.name} -->",
                    f"<!-- role: {role} -->",
                    f"<!-- chunk: {index} -->",
                    (
                        f"<!-- image: {task['image']} -->"
                        if task["image"] else
                        "<!-- image: none -->"
                    ),
                    "",
                ]

                # Salvataggio atomico: niente file parziali
                # se il processo viene interrotto.
                final_output = "\n".join(metadata) + result.strip() + "\n"
                if not has_useful_content(final_output):
                    raise GenerationValidationError("Output finale non valido.")
                atomic_write_text(output_file, final_output)

                if key not in state[source_name]["completed"]:
                    state[source_name]["completed"].append(key)

                state[source_name]["errors"].pop(key, None)

            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                print(f"    ERRORE: {error}")
                state[source_name]["errors"][key] = error

            atomic_write_text(
                state_file,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )

            # Pausa breve fra le richieste.
            time.sleep(1)

    print(f"\nElaborazione terminata. Risultati in: {DRAFT}")
    print(f"Stato: {state_file}")
    print("Controlla state.json per individuare eventuali errori.")


if __name__ == "__main__":
    main()
