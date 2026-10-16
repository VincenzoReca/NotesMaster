from pathlib import Path
import argparse
import json
import re
import sys
import time

import ollama

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
EXTRACTED = OUTPUT / "extracted"
DRAFT = OUTPUT / "draft"
DRAFT_STATE = DRAFT / "state.json"

DEFAULT_MODEL = "qwen3-vl:4b-instruct"
MAX_BATCH_CHARS = 4200
NUM_CTX = 8192
NUM_PREDICT = 4096


class MergeError(RuntimeError):
    pass


def useful(text):
    return isinstance(text, str) and len(text.strip()) >= 40


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MergeError(f"File richiesto non trovato: {path}")
    except json.JSONDecodeError as exc:
        raise MergeError(f"JSON non valido in {path}: {exc}")


def read_sources_and_chunks():
    if not EXTRACTED.is_dir():
        raise MergeError(f"Cartella non trovata: {EXTRACTED}")
    if not DRAFT_STATE.is_file():
        raise MergeError(f"Stato della generazione non trovato: {DRAFT_STATE}")

    extracted_files = sorted(EXTRACTED.glob("*.md"))
    if not extracted_files:
        raise MergeError(f"Nessun Markdown estratto in {EXTRACTED}")

    state = load_json(DRAFT_STATE)
    all_sources = []
    problems = []

    for source_path in extracted_files:
        key = source_path.stem
        source_text = source_path.read_text(encoding="utf-8")
        entry = state.get(key)

        if not isinstance(entry, dict):
            problems.append(f"{source_path.name}: assente da output/draft/state.json")
            continue

        total = entry.get("total")
        errors = entry.get("errors") or {}
        completed = {str(x) for x in entry.get("completed", [])}
        if errors:
            problems.append(f"{source_path.name}: errori registrati: {errors}")
        if not isinstance(total, int) or total <= 0:
            problems.append(f"{source_path.name}: numero di porzioni non valido ({total})")
            continue

        chunk_dir = DRAFT / key
        chunks = []
        missing = []
        for i in range(1, total + 1):
            chunk_path = chunk_dir / f"chunk_{i:04d}.md"
            if not chunk_path.is_file():
                missing.append(str(chunk_path.relative_to(ROOT)))
                continue
            chunk_text = chunk_path.read_text(encoding="utf-8")
            # I metadati HTML sono utili per la tracciabilità, ma non contano come contenuto.
            body = re.sub(r"<!--.*?-->", "", chunk_text, flags=re.DOTALL).strip()
            if len(body) < 20:
                problems.append(f"{source_path.name}: chunk {i} vuoto o troppo corto")
                continue
            if str(i) not in completed:
                problems.append(f"{source_path.name}: chunk {i} esiste ma non risulta completato nello state")
            chunks.append({
                "source": source_path.name,
                "role": entry.get("role", "unclassified"),
                "chunk": i,
                "text": body,
            })

        if missing:
            problems.append(f"{source_path.name}: chunk mancanti: {', '.join(missing)}")
        if len(chunks) != total:
            problems.append(f"{source_path.name}: attesi {total} chunk validi, trovati {len(chunks)}")
        all_sources.append({
            "name": source_path.name,
            "role": entry.get("role", "unclassified"),
            "text": source_text,
            "chunks": chunks,
        })

    if problems:
        detail = "\n".join(f"- {p}" for p in problems)
        raise MergeError(
            "Consolidamento bloccato: la generazione non risulta completa e valida.\n"
            "Correggi prima questi problemi:\n" + detail
        )

    if not all_sources or not any(s["chunks"] for s in all_sources):
        raise MergeError("Non ci sono chunk validi da consolidare.")

    return all_sources


def call_model(model, system_prompt, user_prompt, num_predict=NUM_PREDICT):
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        think=False,
        options={
            "temperature": 0.1,
            "num_ctx": NUM_CTX,
            "num_predict": num_predict,
        },
    )
    content = getattr(response.message, "content", "") or ""
    done_reason = getattr(response, "done_reason", None)
    if not useful(content):
        thinking = getattr(response.message, "thinking", "") or ""
        raise MergeError(
            f"Il modello non ha restituito testo utile "
            f"(thinking_chars={len(thinking)}, done_reason={done_reason})."
        )
    if done_reason == "length":
        raise MergeError("Risposta del modello troncata (done_reason='length').")
    return content.strip()


def pack_chunks(chunks, max_chars):
    batches = []
    current = []
    current_len = 0
    for chunk in chunks:
        block = (
            f"[FONTE: {chunk['source']} | RUOLO: {chunk['role']} | "
            f"CHUNK: {chunk['chunk']}]\n{chunk['text']}"
        )
        if current and current_len + len(block) > max_chars:
            batches.append(current)
            current = []
            current_len = 0
        current.append(block)
        current_len += len(block)
    if current:
        batches.append(current)
    return batches


MERGE_SYSTEM = """Sei un assistente universitario che consolida appunti usando ESCLUSIVAMENTE le fonti fornite.
Non usare conoscenze esterne, completamenti per intuizione o fatti non presenti nei testi.
La sbobina del professore (primary_lecture) e le slide del professore (professor_slides) sono le fonti autorevoli e prevalgono sugli appunti personali (personal_notes).
Se appunti personali contraddicono una fonte del professore, non includere la versione personale come fatto: segnala la discrepanza in una sezione 'Discrepanze e punti da verificare', indicando le fonti.
Se sbobina e slide sono in conflitto tra loro, conserva entrambe le versioni e segnala il conflitto; non scegliere arbitrariamente.
Conserva tutte le definizioni, i dettagli tecnici, i passaggi logici, gli esempi, le formule, le condizioni, le eccezioni e le precisazioni contenuti nei testi forniti. Unisci solo duplicati semanticamente equivalenti.
Non trasformare ipotesi in fatti. Non aggiungere spiegazioni per rendere un concetto più completo.
Scrivi in italiano e in Markdown valido. Usa riferimenti brevi alla fonte tra parentesi quadre, ad esempio [sbobina.md, chunk 2].
Se un ruolo è unclassified, non attribuirgli autorità non dimostrata.
Il tuo output deve essere solo il Markdown consolidato, senza preamboli sul tuo processo."""


def consolidate_chunks(model, chunks):
    batches = pack_chunks(chunks, MAX_BATCH_CHARS)
    partials = []
    for idx, batch in enumerate(batches, start=1):
        print(f"  Consolidamento gruppo {idx}/{len(batches)}...")
        prompt = (
            "Consolida il seguente gruppo di analisi in sezioni di appunti coerenti. "
            "Non perdere dettagli presenti nei testi e non introdurre informazioni esterne. "
            "Mantieni le citazioni alle fonti e crea una sezione 'Discrepanze e punti da verificare' "
            "solo se ci sono discrepanze o incertezze supportate dai testi.\n\n"
            + "\n\n---\n\n".join(batch)
        )
        partials.append(call_model(model, MERGE_SYSTEM, prompt))
        time.sleep(0.5)

    # Se le sezioni intermedie sono numerose, le riunisce in passaggi successivi.
    pass_number = 1
    while sum(len(x) for x in partials) > MAX_BATCH_CHARS:
        next_partials = []
        current = []
        current_len = 0
        for item in partials:
            if current and current_len + len(item) > MAX_BATCH_CHARS:
                prompt = (
                    "Integra queste sezioni intermedie in un unico testo coerente. "
                    "Non eliminare informazioni uniche, non aggiungere fatti esterni e conserva "
                    "tutti i riferimenti alle fonti e le discrepanze.\n\n"
                    + "\n\n---\n\n".join(current)
                )
                next_partials.append(call_model(model, MERGE_SYSTEM, prompt))
                time.sleep(0.5)
                current = []
                current_len = 0
            current.append(item)
            current_len += len(item)
        if current:
            prompt = (
                "Integra queste sezioni intermedie in un unico testo coerente. "
                "Non eliminare informazioni uniche, non aggiungere fatti esterni e conserva "
                "tutti i riferimenti alle fonti e le discrepanze.\n\n"
                + "\n\n---\n\n".join(current)
            )
            next_partials.append(call_model(model, MERGE_SYSTEM, prompt))
            time.sleep(0.5)
        if len(next_partials) >= len(partials):
            # Evita un ciclo infinito se il modello non riduce la dimensione.
            break
        partials = next_partials
        pass_number += 1

    if len(partials) == 1:
        return partials[0]

    prompt = (
        "Produci il documento finale integrando tutte le sezioni seguenti. "
        "Mantieni ogni informazione unica, non aggiungere contenuti esterni, applica la gerarchia "
        "delle fonti e conserva le discrepanze. Non limitarti a riassumere.\n\n"
        + "\n\n---\n\n".join(partials)
    )
    return call_model(model, MERGE_SYSTEM, prompt)


def rewrite_image_links(markdown, extracted_file):
    # I link relativi negli estratti sono relativi a output/extracted.
    # Nel file finale in output diventano relativi a output/.
    def replace(match):
        raw = match.group(2)
        if raw.startswith(("http://", "https://", "#", "data:")):
            return match.group(0)
        normalized = raw.replace("\\", "/")
        if normalized.startswith("../images/"):
            normalized = normalized[len("../"):]
        elif normalized.startswith("images/"):
            pass
        else:
            # Non alterare percorsi relativi non riconosciuti.
            return match.group(0)
        return f"{match.group(1)}{normalized}{match.group(3)}"

    # Markdown images: ![alt](path)
    markdown = re.sub(
        r"(!\[[^\]]*\]\()([^)]+)(\))",
        lambda m: replace(m),
        markdown,
    )
    # Backtick references emitted by the PDF extractor: `../images/...`
    markdown = markdown.replace("`../images/", "`images/")
    return markdown


def build_source_appendix(sources):
    pieces = [
        "\n\n---\n\n# Appendice — contenuti estratti dalle fonti\n",
        (
            "> Questa appendice conserva il testo estratto prima dell'analisi e del consolidamento. "
            "Non è una garanzia che l'estrazione automatica abbia catturato ogni elemento visivo o "
            "strutturale del file originale: per contenuti illeggibili o non estraibili, consultare "
            "il documento sorgente originale."
        ),
    ]
    for source in sources:
        source_text = rewrite_image_links(source["text"], source["name"])
        pieces.extend([
            "\n\n---\n\n",
            f"## Fonte originale: {source['name']}",
            f"\n\n**Ruolo assegnato:** `{source['role']}`\n",
            "\n\n",
            source_text,
        ])
    return "\n".join(pieces)


def main():
    parser = argparse.ArgumentParser(
        description="Consolida le analisi generate da generate_notes.py in un unico Markdown."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--output",
        default=str(OUTPUT / "appunti_finali.md"),
        help="Percorso del Markdown finale.",
    )
    parser.add_argument(
        "--no-appendix",
        action="store_true",
        help="Non aggiunge l'appendice con i testi estratti originali (sconsigliato se la priorità è non perdere contenuti).",
    )
    args = parser.parse_args()

    try:
        sources = read_sources_and_chunks()
        available = ollama.list()
        model_names = {item.model for item in available.models}
        if not any(name.split(":")[0] == args.model.split(":")[0] for name in model_names):
            raise MergeError(
                f"Modello {args.model} non trovato. Controlla `ollama list`."
            )

        chunks = [chunk for source in sources for chunk in source["chunks"]]
        print(f"Fonti: {len(sources)} | chunk validi: {len(chunks)}")
        print("Creo il documento consolidato...")
        merged = consolidate_chunks(args.model, chunks)

        final_parts = [
            "# Appunti consolidati",
            "",
            "> Documento generato localmente a partire dalle fonti estratte. "
            "Le citazioni identificano le fonti usate. Consultare la sezione delle discrepanze "
            "e il registro di verifica prima di considerarlo definitivo.",
            "",
            merged,
        ]
        if not args.no_appendix:
            final_parts.append(build_source_appendix(sources))

        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        atomic_write(output_path, "\n".join(final_parts).strip() + "\n")

        report = {
            "model": args.model,
            "output": str(output_path),
            "sources": [
                {
                    "file": s["name"],
                    "role": s["role"],
                    "chunks": len(s["chunks"]),
                    "source_text_chars": len(s["text"]),
                }
                for s in sources
            ],
            "chunk_count": len(chunks),
            "appendix_included": not args.no_appendix,
            "warning": (
                "La validazione dei chunk garantisce che i risultati intermedi esistano e siano "
                "segnati completati; non dimostra matematicamente che il modello non abbia omesso "
                "o reinterpretato dettagli. L'appendice conserva il testo estratto disponibile."
            ),
        }
        atomic_write(
            OUTPUT / "merge_state.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        print(f"\nDocumento creato: {output_path}")
        print(f"Registro: {OUTPUT / 'merge_state.json'}")
        print("Nota: controlla le discrepanze e confronta le fonti originali prima di considerarlo definitivo.")
    except (MergeError, ollama.ResponseError, ConnectionError) as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
