
from pathlib import Path
from dataclasses import replace
import argparse
import hashlib
import json
import re
import time

from appunti_ai.modeling import ModelRequest, create_client, load_profile
from appunti_ai.modeling.profiles import DEFAULT_PROFILE_FILE, ProfileError
from appunti_ai.retrieval.lexical import RetrievalError, build_index, retrieve

ROOT = Path(__file__).resolve().parents[2]
EXTRACTED = ROOT / "output" / "extracted"
DRAFT = ROOT / "output" / "draft"

MODEL = "qwen3-vl:4b-instruct"
MODEL_PROFILE = None
PROFILE_FILE = DEFAULT_PROFILE_FILE
MAX_CHARS = 3000
NUM_CTX = 4096
NUM_PREDICT = 2500
PROMPT_VERSION = "generation-source-grounded-v3"

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
    if any(marker in text.casefold() for marker in ("[truncated]", "[output truncated]", "[risposta troncata]", "[da verificare]")):
        return False
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


def generation_fingerprint(profile, shared_index):
    payload = {
        "prompt_version": PROMPT_VERSION,
        "profile": profile.name,
        "backend": profile.backend,
        "model": profile.model,
        "capabilities": sorted(profile.capabilities),
        "options": profile.options,
        "shared_manifest_sha256": shared_index.get("manifest_sha256") if shared_index else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def task_input_hash(task):
    payload = {"text": task.get("text", ""), "image": str(task.get("image")) if task.get("image") else None}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


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


def make_prompt(role, filename, chunk_number, text, has_image, shared_context=""):
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
NON presentare queste affermazioni come fatti verificati. Do not insert process
placeholders such as [DA VERIFICARE] into the study text; preserve the
uncertainty in neutral prose or leave the claim for the consolidation audit.
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

    shared_block = (
        "\n\nPASSAGGI PERTINENTI DA FONTI CONDIVISE:\n"
        + shared_context
        + "\nUsali solo come contesto complementare e mantieni sempre la loro provenienza."
        if shared_context else ""
    )
    return f"""
Stai scrivendo una sezione di un capitolo universitario completo.

File: {filename}
Fonte: {role}
Questa porzione è materiale sorgente: non menzionare porzioni, chunk, prompt o processo di elaborazione nel testo prodotto.

REGOLE DELLA FONTE:
{source_policy}

REGOLE VISIVE:
{visual_policy}

IMAGE REQUIREMENT: When an image is attached, explain its visible content and compare it with every available textual source. Never invent unreadable details. If a useful interpretation exists only in the image and nowhere in the textual sources, mark it exactly as [VISUAL INTERPRETATION — NOT STATED IN SOURCE]. Use this marker only in that case.

ISTRUZIONI:
1. Write in English, with precise technical terminology.
2. Conserva definizioni, formule, esempi e dettagli importanti.
3. Organizza il risultato con titoli Markdown.
4. Non aggiungere nozioni esterne alla fonte.
5. Se un testo o una figura non sono chiari, conserva solo ciò che è supportato.
6. Scrivi in forma discorsiva e didattica, come un libro di testo o appunti universitari rifiniti.
7. Non parlare della fonte in terza persona e non descrivere il tuo ragionamento: spiega direttamente i concetti.
8. Non inserire riferimenti a chunk, porzioni, prompt, metadati o istruzioni interne.
9. Non trasformare ipotesi in fatti.

CONFLICT REQUIREMENT: Treat professor lecture transcripts and professor slides as authoritative over personal notes. Do not present a conflicting personal-note claim as established content. When a material conflict is supported by the supplied texts, preserve the authoritative formulation and add a concise English note exactly in this form: > **Source clarification.** ... . Identify the conflicting formulations without mentioning the model or the processing pipeline. Conflicts between professor sources must not be resolved by invention; retain both claims and flag the disagreement for review.

TESTO DELLA FONTE:
{text}
{shared_block}

Produce autonomous, coherent English prose with Markdown headings only when useful. Do not analyse or describe the source: write the actual study notes directly.
""".strip()


def process_task(role, filename, number, task, client=None, shared_context=""):
    prompt = make_prompt(
        role=role,
        filename=filename,
        chunk_number=number,
        text=task["text"],
        has_image=task["image"] is not None,
        shared_context=shared_context,
    )

    message = {
        "role": "user",
        "content": prompt,
    }

    if task["image"] is not None:
        message["images"] = [str(task["image"])]

    if client is None:
        profile = load_profile(MODEL_PROFILE, PROFILE_FILE)
        if profile.model != MODEL:
            profile = replace(profile, model=MODEL)
        client = create_client(profile)
    response = client.generate(
        ModelRequest(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are writing rigorous English university study notes. "
                        "Preserve source fidelity and never invent content. "
                        "Write direct explanatory prose, not commentary on the source."
                    ),
                },
                message,
            ],
            required_capabilities=frozenset({"text", "vision"} if task["image"] is not None else {"text"}),
        )
    )

    content = response.content
    if not has_useful_content(content):
        raise GenerationValidationError(
            "Risposta senza contenuto utile "
            f"(provider={response.provider}, model={response.model})."
        )
    if response.done_reason == "length":
        raise GenerationValidationError("Risposta troncata (done_reason='length').")
    return content


def main():
    global MODEL, MODEL_PROFILE, PROFILE_FILE, EXTRACTED, DRAFT
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="Override legacy del model nel profilo selezionato.")
    parser.add_argument("--model-profile", help="Profilo in model_profiles.json.")
    parser.add_argument("--profiles", type=Path, default=PROFILE_FILE, help="File JSON dei profili.")
    parser.add_argument("--allow-remote", action="store_true", help="Consente esplicitamente profili remoti.")
    parser.add_argument("--source", help="Markdown estratto da elaborare.")
    parser.add_argument("--max-tasks", type=int, help="Limite porzioni per fonte.")
    parser.add_argument("--force", action="store_true", help="Rigenera anche chunk gia' validi.")
    parser.add_argument("--extracted-dir", type=Path, help="Directory degli estratti della sezione.")
    parser.add_argument("--draft-dir", type=Path, help="Directory dei draft e dello state della sezione.")
    parser.add_argument("--shared-output", type=Path, help="Archivio output delle fonti condivise indicizzato.")
    parser.add_argument("--shared-limit", type=int, default=3, help="Numero massimo di passaggi condivisi per richiesta.")
    args = parser.parse_args()
    if args.max_tasks is not None and args.max_tasks < 1:
        raise SystemExit("--max-tasks deve essere positivo.")
    if args.extracted_dir:
        EXTRACTED = args.extracted_dir.resolve()
    if args.draft_dir:
        DRAFT = args.draft_dir.resolve()
    MODEL_PROFILE = args.model_profile
    PROFILE_FILE = args.profiles
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

    try:
        profile = load_profile(MODEL_PROFILE, PROFILE_FILE)
        if args.model:
            profile = replace(profile, model=args.model)
        MODEL = profile.model
        client = create_client(profile, allow_remote=args.allow_remote)
    except ProfileError as exc:
        raise SystemExit(f"Profilo modello non valido: {exc}") from exc

    shared_index = None
    if args.shared_output:
        if args.shared_limit < 1:
            raise SystemExit("--shared-limit deve essere positivo.")
        try:
            shared_index = build_index(args.shared_output)
        except (RetrievalError, OSError, ValueError) as exc:
            raise SystemExit(f"Archivio condiviso non valido: {exc}") from exc

    state_file = DRAFT / "state.json"
    DRAFT.mkdir(parents=True, exist_ok=True)

    state = {}
    if state_file.exists():
        try:
            state = json.loads(
                state_file.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            raise SystemExit(
                f"State JSON non valido: {state_file}. "
                "Il file non verrà sovrascritto; correggilo o ripristina un backup."
            )

    fingerprint = generation_fingerprint(profile, shared_index)
    metadata = state.get("_meta") if isinstance(state.get("_meta"), dict) else {}
    if metadata.get("fingerprint") != fingerprint:
        for entry in state.values():
            if isinstance(entry, dict) and isinstance(entry.get("completed"), list):
                entry["completed"] = []
        print("Dipendenze cambiate: i completamenti precedenti saranno rivalidati.")
    state["_meta"] = {
        "fingerprint": fingerprint,
        "profile": profile.name,
        "model": profile.model,
        "shared_manifest_sha256": shared_index.get("manifest_sha256") if shared_index else None,
    }

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
            "tasks": {},
        })
        state[source_name].setdefault("tasks", {})

        state[source_name]["total"] = len(tasks)

        for index, task in enumerate(tasks, start=1):
            if args.max_tasks is not None and index > args.max_tasks:
                break
            output_file = source_dir / f"chunk_{index:04d}.md"
            key = str(index)
            input_hash = task_input_hash(task)
            task_record = state[source_name]["tasks"].get(key)

            # Un risultato già salvato viene riutilizzato.
            reusable = (not args.force and is_valid_output(output_file)
                        and isinstance(task_record, dict)
                        and task_record.get("input_hash") == input_hash
                        and task_record.get("fingerprint") == fingerprint)
            if reusable:
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
                shared_context = ""
                if shared_index is not None:
                    passages = retrieve(shared_index, f"{role} {source_file.stem} {task['text']}", limit=args.shared_limit)
                    shared_context = "\n\n---\n\n".join(
                        f"[Fonte condivisa: {item['source']} | blocco {item['chunk']}]\n{item['text']}"
                        for item in passages
                    )
                result = process_task(
                    role=role,
                    filename=source_file.name,
                    number=index,
                    task=task,
                    client=client,
                    shared_context=shared_context,
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
                if task["image"]:
                    try:
                        image_reference = Path(task["image"]).resolve().relative_to(DRAFT.parent.resolve()).as_posix()
                        metadata.extend([f"![Source figure]({image_reference})", ""])
                    except ValueError:
                        raise GenerationValidationError("Image path is outside the section output root.")

                # Salvataggio atomico: niente file parziali
                # se il processo viene interrotto.
                final_output = "\n".join(metadata) + result.strip() + "\n"
                if not has_useful_content(final_output):
                    raise GenerationValidationError("Output finale non valido.")
                atomic_write_text(output_file, final_output)

                if key not in state[source_name]["completed"]:
                    state[source_name]["completed"].append(key)

                state[source_name]["errors"].pop(key, None)
                state[source_name]["tasks"][key] = {
                    "status": "completed", "input_hash": input_hash,
                    "fingerprint": fingerprint, "output": str(output_file),
                    "validation": "useful_content_and_not_truncated",
                }

            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                print(f"    ERRORE: {error}")
                state[source_name]["errors"][key] = error
                state[source_name]["tasks"][key] = {
                    "status": "needs_review" if isinstance(exc, GenerationValidationError) else "failed", "input_hash": input_hash,
                    "fingerprint": fingerprint, "output": str(output_file),
                    "error": error,
                }

            atomic_write_text(
                state_file,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )

            # Pausa breve fra le richieste.
            time.sleep(1)

    print(f"\nElaborazione terminata. Risultati in: {DRAFT}")
    print(f"Stato: {state_file}")
    audit_dir = DRAFT.parent / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "version": 1,
        "profile": profile.name,
        "model": profile.model,
        "fingerprint": fingerprint,
        "sources": {
            name: {
                "total": entry.get("total"),
                "completed": len(entry.get("completed", [])),
                "failed": len(entry.get("errors", {})),
            }
            for name, entry in state.items()
            if name != "_meta" and isinstance(entry, dict)
        },
    }
    atomic_write_text(audit_dir / "generation_audit.json", json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print("Controlla state.json per individuare eventuali errori.")


if __name__ == "__main__":
    main()
