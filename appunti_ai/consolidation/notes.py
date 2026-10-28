from pathlib import Path
import argparse
import json
import re
import sys
import time
import hashlib

from dataclasses import replace

from appunti_ai.modeling.client import ModelRequest, ModelClient, create_client
from appunti_ai.modeling.profiles import DEFAULT_PROFILE_FILE, ProfileError, load_profile

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output"
EXTRACTED = OUTPUT / "extracted"
DRAFT = OUTPUT / "draft"
DRAFT_STATE = DRAFT / "state.json"

DEFAULT_MODEL = "qwen3-vl:4b-instruct"
MAX_BATCH_CHARS = 4200
NUM_CTX = 8192
NUM_PREDICT = 4096
MAX_MERGE_DEPTH = 8
FINAL_REVIEW_MAX_CHARS = 16000
MERGE_CACHE_DIR = None


class MergeError(RuntimeError):
    pass


def validate_consolidated_content(text, chunks):
    """Controlli deterministici prudenti: non sostituiscono la revisione umana."""
    issues = []
    stripped = text.strip() if isinstance(text, str) else ""
    if len(stripped) < 80:
        issues.append("final_output_too_short")
    if not re.search(r"^#{1,6}\s+\S+", stripped, re.MULTILINE):
        issues.append("missing_markdown_heading")
    if re.search(r"\[truncated\]|<think>|chunk[_ ]?\d+|\[da verificare\]", stripped, re.IGNORECASE):
        issues.append("process_or_truncation_marker")
    input_chars = sum(len(chunk.get("text", "")) for chunk in chunks)
    if input_chars and len(stripped) < max(80, int(input_chars * 0.08)):
        issues.append("output_much_shorter_than_inputs")
    return {"status": "needs_review" if issues else "passed", "issues": issues, "input_chars": input_chars, "output_chars": len(stripped), "human_review_required": True}


def repair_mojibake(text: str) -> str:
    """Repair the common UTF-8-as-Windows-1252 artifact without touching valid text."""
    if not isinstance(text, str) or not any(marker in text for marker in ("â", "Ã", "Â")):
        return text
    repaired = text
    for _ in range(2):
        try:
            candidate = repaired.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate.count("�") > repaired.count("�"):
            break
        repaired = candidate
        if not any(marker in repaired for marker in ("â", "Ã", "Â")):
            break
    return repaired


def normalize_image_links(text: str) -> tuple[str, list[str]]:
    """Resolve model-altered image paths against the section's real image tree."""
    unresolved = []
    image_root = (OUTPUT / "images").resolve()
    by_name = {path.name: path for path in image_root.rglob("*") if path.is_file()} if image_root.is_dir() else {}

    def replace(match):
        alt, raw = match.group(1), match.group(2).replace("\\", "/")
        if raw.startswith(("http://", "https://", "data:", "#")):
            return match.group(0)
        candidates = [(OUTPUT / raw).resolve(), (OUTPUT / raw.lstrip("../")).resolve()]
        target = next((p for p in candidates if p.is_file()), None)
        if target is None:
            target = by_name.get(Path(raw).name)
        if target is None:
            unresolved.append(raw)
            return match.group(0)
        relative = target.relative_to(OUTPUT.resolve()).as_posix()
        return f"![{alt}]({relative})"

    normalized = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace, text)
    seen = set()
    lines = []
    for line in normalized.splitlines():
        match = re.match(r"^!\[[^\]]*\]\(([^)]+)\)\s*$", line.strip())
        if match and match.group(1) in seen:
            continue
        if match:
            seen.add(match.group(1))
        lines.append(line)
    return "\n".join(lines).strip(), sorted(set(unresolved))


def sanitize_process_metadata(text: str) -> str:
    """Remove source/chunk bookkeeping echoed by the model, preserving prose."""
    text = re.sub(r"\[\s*(?:FONTE|SOURCE)\s*:[^\]\n]*?(?:CHUNK|CHUNK_ID)\s*[:=]?\s*\d+\s*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[[^\]\n,]+(?:\.[A-Za-z0-9]+)?\s*,\s*(?:chunk|source chunk)\s*\d+\s*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:(?:—|–|-|â€”|â€“)\s*)?[^\n\[\]]+\.(?:pdf|docx|md|txt)\s*,\s*chunk\s*\d+\s*(?:—|–|-|â€”|â€“)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[(?:professor\s+slides|personal\s+notes|primary\s+lecture|unclassified)\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def unify_discrepancy_sections(text: str) -> str:
    """Keep one final discrepancy section when multiple merge passes created duplicates."""
    pattern = re.compile(r"^#{1,6}\s+Discrepancies and Points to Verify\s*$", re.IGNORECASE | re.MULTILINE)
    matches = list(pattern.finditer(text))
    if len(matches) <= 1:
        return text
    bodies = []
    base_parts = []
    cursor = 0
    for index, match in enumerate(matches):
        base_parts.append(text[cursor:match.start()])
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            bodies.append(body)
        cursor = end
    base = "".join(base_parts).rstrip()
    unique = []
    for body in bodies:
        if body not in unique:
            unique.append(body)
    return base + "\n\n## Discrepancies and Points to Verify\n\n" + "\n\n".join(unique)


def deduplicate_headings(text: str) -> str:
    """Drop repeated identical headings while keeping their section content."""
    seen = set()
    lines = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            lines.append(line)
            continue
        title = re.sub(r"\s+", " ", match.group(2)).casefold()
        if title in seen and title != "discrepancies and points to verify":
            continue
        seen.add(title)
        lines.append(line)
    return "\n".join(lines).strip()


def coverage_audit(text, chunks, *, minimum_overlap: float = 0.12):
    """Estima la copertura lessicale dei chunk, senza sostituire la revisione umana."""
    output_terms = set(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}", text.casefold()))
    details = []
    for index, chunk in enumerate(chunks, start=1):
        terms = set(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}", str(chunk.get("text", "")).casefold()))
        overlap = len(terms & output_terms) / len(terms) if terms else 1.0
        details.append({"chunk": index, "source": chunk.get("source"), "term_count": len(terms), "overlap": round(overlap, 4), "status": "ok" if overlap >= minimum_overlap else "low"})
    low = [item for item in details if item["status"] == "low"]
    return {"status": "needs_review" if low else "passed", "minimum_overlap": minimum_overlap, "chunks": details, "low_coverage_chunks": len(low), "human_review_required": True}


def preserve_image_blocks(merged: str, chunks: list[dict[str, object]]) -> str:
    """Ensure every model-generated image explanation remains visible in the final."""
    image_blocks = []
    for chunk in chunks:
        text = str(chunk.get("text", ""))
        if re.search(r"!\[[^\]]*\]\([^)]*\)", text):
            image_blocks.append(text.strip())
    missing = [block for block in image_blocks if not re.search(r"!\[[^\]]*\]\([^)]*\)", merged) or any(
        image not in merged for image in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", block)
    )]
    if not missing:
        return merged
    return merged.rstrip() + "\n\n## Visual material\n\n" + "\n\n---\n\n".join(missing)


FINAL_REVIEW_SYSTEM = """You are the final source-fidelity reviewer for a university study chapter.
The SOURCE PACKET is the only allowed factual authority. Rewrite the chapter against it, not against general knowledge.
For EVERY source chunk, preserve all distinct definitions, motivations, reasoning steps, examples, formulas, code, exceptions, limitations, qualifications, and image explanations that are relevant. Do not merely match keywords: compare claims and relationships semantically.
Remove or rewrite every claim that cannot be supported by the SOURCE PACKET. Do not add outside knowledge, standard textbook completion, plausible security implications, typical properties, or inferred facts. If a source is uncertain, preserve that uncertainty without inventing a resolution.
Remove process metadata, model reasoning, chunk/file commentary, [DA VERIFICARE], [truncated], and similar placeholders.
For every required image reference, keep the exact path and keep a concise explanation immediately before or after it. Explain only visible/supportable content.
Keep a material source conflict as a concise '> **Source clarification.** ...' note. Do not treat complementary information as a conflict.
Return only the complete Markdown chapter. Never return a review, checklist, preamble, or explanation of your changes."""


def final_review(text: str, chunks: list[dict[str, object]], model: str, client) -> tuple[str, dict[str, object]]:
    if len(text) > FINAL_REVIEW_MAX_CHARS:
        raise MergeError(f"Final review input exceeds the bounded limit ({FINAL_REVIEW_MAX_CHARS} characters).")
    image_paths = sorted({path for chunk in chunks for path in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", str(chunk.get("text", "")))})
    image_files = []
    for path in image_paths:
        candidate = (EXTRACTED / path).resolve()
        if candidate.is_file():
            image_files.append(candidate)
    packet_parts = []
    packet_chars = 0
    packet_records = []
    for index, chunk in enumerate(chunks, start=1):
        source_text = str(chunk.get("text", ""))
        digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16]
        packet_parts.append(f"SOURCE CHUNK {index} | id={digest} | source={chunk.get('source')} | role={chunk.get('role')}\n{source_text}")
        packet_chars += len(source_text)
        packet_records.append({"index": index, "id": digest, "source": chunk.get("source"), "role": chunk.get("role"), "chars": len(source_text)})
    if packet_chars > FINAL_REVIEW_MAX_CHARS:
        raise MergeError(f"Final review source packet exceeds the bounded limit ({FINAL_REVIEW_MAX_CHARS} characters).")
    checklist = "\n".join(f"- Required image path: {path}" for path in image_paths) or "- No image references are required."
    prompt = (
        "FINAL REVIEW CHECKLIST:\n" + checklist +
        "\n\nSOURCE PACKET (the sole factual authority):\n" + "\n\n---\n\n".join(packet_parts) +
        "\n\nCHAPTER TO REVIEW:\n" + text
    )
    if len(prompt) > FINAL_REVIEW_MAX_CHARS * 2:
        raise MergeError("Final review prompt exceeds the bounded combined source/chapter limit.")
    if image_paths and len(image_files) != len(image_paths):
        raise MergeError("Final review cannot verify every referenced image file.")
    reviewed = call_model(model, FINAL_REVIEW_SYSTEM, prompt, num_predict=NUM_PREDICT, client=client, images=image_files)
    reviewed = preserve_image_blocks(reviewed, chunks)
    validation = validate_consolidated_content(reviewed, chunks)
    missing_images = [path for path in image_paths if path not in reviewed]
    if missing_images:
        validation["issues"].append("missing_image_references")
        validation["status"] = "needs_review"
    validation["image_paths"] = image_paths
    validation["missing_image_paths"] = missing_images
    validation["verified_image_files"] = [str(path) for path in image_files]
    validation["source_packet"] = packet_records
    # A review warning is an audit outcome, not a reason to discard the
    # chapter.  The caller persists the reviewed text and records the issues
    # so that a human can inspect them.  Hard failures (model/packet errors)
    # still raise before reaching this point.
    return reviewed, validation


def write_merge_checkpoint(stage, pass_number, items, *, status="running"):
    payload = "\n".join(str(item) for item in items).encode("utf-8")
    checkpoint = {
        "version": 1,
        "status": status,
        "stage": stage,
        "pass": pass_number,
        "items": len(items),
        "chars": len(payload.decode("utf-8")),
        "input_fingerprint": hashlib.sha256(payload).hexdigest(),
    }
    atomic_write(OUTPUT / "merge_checkpoint.json", json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n")
    return checkpoint


def cached_merge_call(cache_key, model, system_prompt, user_prompt, client):
    cache_dir = MERGE_CACHE_DIR or (OUTPUT / "audit" / "merge_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    full_key = hashlib.sha256((cache_key + "\n" + model + "\n" + system_prompt).encode("utf-8")).hexdigest()
    cache_path = cache_dir / f"{full_key}.md"
    if cache_path.is_file():
        cached = cache_path.read_text(encoding="utf-8").strip()
        if useful(cached):
            return cached
    result = call_model(model, system_prompt, user_prompt, client=client)
    atomic_write(cache_path, result + "\n")
    return result


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


def call_model(model, system_prompt, user_prompt, num_predict=NUM_PREDICT, client=None, images=None):
    if client is None:
        profile = load_profile("local_qwen", DEFAULT_PROFILE_FILE)
        if model and model != profile.model:
            profile = replace(profile, model=model)
        client = create_client(profile)
    user_message = {"role": "user", "content": user_prompt}
    if images:
        user_message["images"] = [str(path) for path in images]
    request_options = {}
    # These are Ollama-specific controls. Azure profiles keep their own
    # provider-valid options (for example max_completion_tokens) and must not
    # receive num_ctx/num_predict/temperature overrides from consolidation.
    if getattr(getattr(client, "profile", None), "backend", None) == "ollama":
        request_options = {"temperature": 0.1, "num_ctx": NUM_CTX, "num_predict": num_predict}
    response = client.generate(ModelRequest(
        messages=[{"role": "system", "content": system_prompt}, user_message],
        required_capabilities=frozenset({"text", "vision"}) if images else frozenset({"text"}),
        options=request_options,
    ))
    content = response.content or ""
    done_reason = response.done_reason
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


def deterministic_partial_document(partials: list[str]) -> str:
    """Build a safe fallback when bounded model merges stop reducing output.

    The partials have already passed the per-request model validation. Keeping
    them in order is preferable to discarding the section or retrying an
    identical oversized merge indefinitely.
    """
    usable = [str(item).strip() for item in partials if str(item).strip()]
    if not usable:
        raise MergeError("Nessun consolidato intermedio valido disponibile.")
    return "\n\n---\n\n".join(usable)


def consolidate_batch_with_split(batch: list[str], *, model: str, client, stage: str, depth: int = 0) -> list[str]:
    """Consolidate a batch, splitting only when the provider truncates output."""
    prompt = (
        "Turn this analysis group into a coherent textbook subsection. Preserve every distinct detail, "
        "remove repetition, do not add external information, and do not mention files, chunks, sources, "
        "or processing. Keep image references beside their explanations.\n\n" + "\n\n---\n\n".join(batch)
    )
    cache_key = hashlib.sha256((stage + "\n" + "\n".join(batch)).encode("utf-8")).hexdigest()
    try:
        return [cached_merge_call(cache_key, model, MERGE_SYSTEM, prompt, client)]
    except MergeError as exc:
        if "done_reason='length'" not in str(exc) and "truncated" not in str(exc).lower():
            raise
        if len(batch) <= 1 or depth >= 6:
            raise MergeError(f"Truncation persisted after deterministic splitting ({stage}, depth={depth}).") from exc
        midpoint = max(1, len(batch) // 2)
        print(f"  Risposta troncata in {stage}: divido il gruppo ({len(batch)} -> {midpoint}+{len(batch)-midpoint}).")
        left = consolidate_batch_with_split(batch[:midpoint], model=model, client=client, stage=stage + ".a", depth=depth + 1)
        right = consolidate_batch_with_split(batch[midpoint:], model=model, client=client, stage=stage + ".b", depth=depth + 1)
        return left + right


MERGE_SYSTEM = """You are an academic author writing a polished, autonomous university textbook chapter using ONLY the supplied sources.
EDITORIAL CONTRACT (repeat this at every consolidation pass): build one coherent chapter, not a collection of source summaries. First identify the concepts and their relationships, then organize them into a small hierarchy of meaningful chapters and subsections. Use a small number of stable headings and place each concept only once in its most logical location. Merge semantically identical passages once, preserve distinct qualifications and examples, and avoid repeating introductions, definitions, or conclusions. Use connected explanatory prose; use lists only when they improve comprehension. Keep one final section named 'Discrepancies and Points to Verify' only when a supported conflict or uncertainty exists; do not create that heading for every local block.
STYLE CONTRACT: write like a clear university textbook chapter, not like notes about notes. Begin with context and motivation when supplied, develop concepts in a logical sequence, connect paragraphs explicitly, and end sections naturally. Never write 'the source says', 'the notes indicate', 'this chunk', or any third-person description of the input material. Keep each image reference next to the concept it illustrates and put its concise, source-grounded explanation immediately beside it.
CONFLICT POLICY: Professor lecture transcripts and professor slides are authoritative over personal notes. Never present a conflicting personal-note claim as established content. When a material conflict is supported by the supplied texts, keep the authoritative formulation and add a concise English note exactly in this form: > **Source clarification.** ... . Identify the conflicting formulations without mentioning the model or processing pipeline. If professor sources conflict with each other, preserve both claims and flag the disagreement; never invent a reconciliation.
Non usare conoscenze esterne, completamenti per intuizione o fatti non presenti nei testi.
La sbobina del professore (primary_lecture) e le slide del professore (professor_slides) sono le fonti autorevoli e prevalgono sugli appunti personali (personal_notes).
If personal notes contradict a professor source, do not include the personal version as fact: preserve the conflict in the single final section 'Discrepancies and Points to Verify', identifying the formulations without process commentary.
Se sbobina e slide sono in conflitto tra loro, conserva entrambe le versioni e segnala il conflitto; non scegliere arbitrariamente.
Conserva tutte le definizioni, i dettagli tecnici, i passaggi logici, gli esempi, le formule, le condizioni, le eccezioni e le precisazioni contenuti nei testi forniti. Unisci solo duplicati semanticamente equivalenti.
Non trasformare ipotesi in fatti. Non aggiungere spiegazioni per rendere un concetto più completo.
Write in English and valid Markdown, using connected explanatory prose, coherent chapters and subsections. Do not mention files, chunks, excerpts, sources, or the processing procedure in the final text. Do not copy headings mechanically from each input block and do not restart the chapter for each block.
When an image is present, explain its visible content and compare it with textual material. Never invent unreadable details. If a useful interpretation is absent from all textual sources, mark it exactly as [VISUAL INTERPRETATION — NOT STATED IN SOURCE], and use that marker only in this case.
Preserve every Markdown image reference supplied in the material. Do not remove, replace, or invent image paths; keep each image close to the explanation of the figure.
Se un ruolo è unclassified, non attribuirgli autorità non dimostrata.
Your output must contain only the Markdown chapter, with no process preamble, no third-person source analysis, and no chunk references."""


def consolidate_chunks(model, chunks, client=None):
    batches = pack_chunks(chunks, MAX_BATCH_CHARS)
    partials = []
    for idx, batch in enumerate(batches, start=1):
        print(f"  Consolidamento gruppo {idx}/{len(batches)}...")
        write_merge_checkpoint("batch", idx, batch)
        partials.extend(consolidate_batch_with_split(batch, model=model, client=client, stage=f"batch-{idx}"))
        time.sleep(0.5)

    # Se le sezioni intermedie sono numerose, le riunisce in passaggi successivi.
    pass_number = 1
    while sum(len(x) for x in partials) > MAX_BATCH_CHARS and pass_number <= MAX_MERGE_DEPTH:
        next_partials = []
        current = []
        current_len = 0
        for item in partials:
            if current and current_len + len(item) > MAX_BATCH_CHARS:
                prompt = (
                    "Integrate these intermediate sections into one coherent textbook chapter. "
                    "Do not remove unique information or add external facts. Eliminate repeated introductions "
                    "and definitions, maintain a logical heading hierarchy, and keep images beside their explanations. "
                    "Do not mention files, chunks, sources, or processing.\n\n"
                    + "\n\n---\n\n".join(current)
                )
                next_partials.extend(consolidate_batch_with_split(current, model=model, client=client, stage=f"hierarchical-{pass_number}"))
                time.sleep(0.5)
                current = []
                current_len = 0
            current.append(item)
            current_len += len(item)
        if current:
            prompt = (
                "Integrate these intermediate sections into one coherent textbook chapter. "
                "Do not remove unique information or add external facts. Eliminate repeated introductions "
                "and definitions, maintain a logical heading hierarchy, and keep images beside their explanations. "
                "Do not mention files, chunks, sources, or processing.\n\n"
                + "\n\n---\n\n".join(current)
            )
            next_partials.extend(consolidate_batch_with_split(current, model=model, client=client, stage=f"hierarchical-{pass_number}"))
            time.sleep(0.5)
        if len(next_partials) >= len(partials):
            # Evita un ciclo infinito se il modello non riduce la dimensione.
            break
        partials = next_partials
        pass_number += 1
        write_merge_checkpoint("hierarchical", pass_number, partials)

    if sum(len(x) for x in partials) > MAX_BATCH_CHARS and len(partials) > 1:
        # Do not discard valid work when the model cannot compress it further.
        # The final review remains best-effort and records any coverage
        # warning; this deterministic fallback guarantees a publishable file.
        print(
            f"  Budget di consolidamento raggiunto dopo {MAX_MERGE_DEPTH} passaggi; "
            "uso i blocchi intermedi validati in ordine."
        )
        return deterministic_partial_document(partials)

    if len(partials) == 1:
        return partials[0]

    prompt = (
        "Produce the final autonomous university chapter by integrating all sections below. "
        "Preserve every unique detail, do not add external content, apply the source hierarchy, "
        "remove repeated introductions and definitions, and keep one final discrepancy section when needed. "
        "Do not merely summarize. Do not mention files, chunks, sources, or the processing procedure.\n\n"
        + "\n\n---\n\n".join(partials)
    )
    final_parts = consolidate_batch_with_split(partials, model=model, client=client, stage="final")
    return final_parts[0] if len(final_parts) == 1 else deterministic_partial_document(final_parts)


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
    global OUTPUT, EXTRACTED, DRAFT, DRAFT_STATE, MERGE_CACHE_DIR
    parser = argparse.ArgumentParser(
        description="Consolida le analisi generate da generate_notes.py in un unico Markdown."
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-profile", default="local_qwen")
    parser.add_argument("--profiles", default=str(DEFAULT_PROFILE_FILE))
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--extracted-dir", type=Path, help="Directory degli estratti della sezione.")
    parser.add_argument("--draft-dir", type=Path, help="Directory dei draft della sezione.")
    parser.add_argument("--output-root", type=Path, help="Directory output della sezione.")
    parser.add_argument("--cache-dir", type=Path, help="Cache del consolidamento isolata per profilo.")
    parser.add_argument(
        "--output",
        default=None,
        help="Percorso del Markdown finale.",
    )
    parser.add_argument(
        "--appendix",
        action="store_true",
        help="Non aggiunge l'appendice con i testi estratti originali (sconsigliato se la priorità è non perdere contenuti).",
    )
    args = parser.parse_args()
    if args.extracted_dir:
        EXTRACTED = args.extracted_dir.resolve()
    if args.draft_dir:
        DRAFT = args.draft_dir.resolve()
        DRAFT_STATE = DRAFT / "state.json"
    if args.output_root:
        OUTPUT = args.output_root.resolve()
    if args.cache_dir:
        MERGE_CACHE_DIR = args.cache_dir.resolve()
    output_argument = args.output or str(OUTPUT / "appunti_finali.md")

    try:
        sources = read_sources_and_chunks()
        profile = load_profile(args.model_profile, Path(args.profiles))
        if args.model:
            profile = replace(profile, model=args.model)
        client = create_client(profile, allow_remote=args.allow_remote)

        chunks = [chunk for source in sources for chunk in source["chunks"]]
        print(f"Fonti: {len(sources)} | chunk validi: {len(chunks)}")
        print("Creo il documento consolidato...")
        merged = consolidate_chunks(profile.model, chunks, client=client)
        merged = preserve_image_blocks(merged, chunks)
        pre_review_merged = merged
        print("Eseguo la revisione finale bounded...")
        try:
            merged, final_review_report = final_review(merged, chunks, profile.model, client)
        except MergeError as exc:
            # Keep the best deterministic consolidation when the bounded
            # review cannot run (for example an oversized packet or a missing
            # image artifact).  Never lose the section final for an audit-only
            # warning; record the failure explicitly below.
            merged = pre_review_merged
            final_review_report = {
                "status": "needs_review",
                "issues": ["final_review_failed"],
                "error": str(exc),
                "human_review_required": True,
            }
        # Publication sanitation is deterministic and runs even when the
        # bounded global review is skipped because the packet is too large.
        merged = repair_mojibake(merged)
        merged = sanitize_process_metadata(merged)
        merged = unify_discrepancy_sections(merged)
        merged = deduplicate_headings(merged)
        merged = preserve_image_blocks(merged, chunks)
        # Normalize after preservation as that fallback may reinsert source
        # image references using paths relative to the extracted directory.
        merged, unresolved_images = normalize_image_links(merged)
        content_validation = validate_consolidated_content(merged, chunks)
        if unresolved_images:
            content_validation["issues"].append("unresolved_image_references")
            content_validation["status"] = "needs_review"
        content_coverage = coverage_audit(merged, chunks)
        review_status = "passed"
        review_issues = []
        for result in (final_review_report, content_validation, content_coverage):
            if result.get("status") != "passed":
                review_status = "needs_review"
                review_issues.extend(result.get("issues", []))
        # Validation and coverage remain visible in audit, but do not prevent
        # publication of the best available Markdown chapter.

        # The study text must contain only the reviewed chapter, not pipeline metadata.
        final_parts = [merged]
        if args.appendix:
            final_parts.append(build_source_appendix(sources))

        output_path = Path(output_argument)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        atomic_write(output_path, "\n".join(final_parts).strip() + "\n")

        report = {
            "model": profile.model,
            "model_profile": profile.name,
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
            "appendix_included": args.appendix,
            "warning": (
                "La validazione dei chunk garantisce che i risultati intermedi esistano e siano "
                "segnati completati; non dimostra matematicamente che il modello non abbia omesso "
                "o reinterpretato dettagli. L'appendice conserva il testo estratto disponibile."
            ),
            "content_validation": content_validation,
            "content_coverage": content_coverage,
            "final_review": final_review_report,
            "publication_status": review_status,
            "publication_issues": sorted(set(review_issues)),
            "unresolved_image_references": unresolved_images,
        }
        atomic_write(
            OUTPUT / "merge_state.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        write_merge_checkpoint("completed", 0, [merged], status="completed")
        audit_dir = OUTPUT / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            audit_dir / "merge_audit.json",
            json.dumps({
                "version": 1,
                "output": str(output_path),
                "sources": report["sources"],
                "chunk_count": report["chunk_count"],
                "appendix_included": report["appendix_included"],
                "warning": report["warning"],
                "content_validation": report["content_validation"],
                "content_coverage": report["content_coverage"],
                "final_review": report["final_review"],
                "publication_status": report["publication_status"],
                "publication_issues": report["publication_issues"],
                "unresolved_image_references": report["unresolved_image_references"],
            }, ensure_ascii=False, indent=2) + "\n",
        )
        print(f"\nDocumento creato: {output_path}")
        print(f"Registro: {OUTPUT / 'merge_state.json'}")
        print("Nota: controlla le discrepanze e confronta le fonti originali prima di considerarlo definitivo.")
    except (MergeError, ProfileError, ConnectionError) as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
