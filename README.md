# NotesMaster

For a practical setup and usage walkthrough, see [USER_GUIDE.md](USER_GUIDE.md).

NotesMaster is a local-first pipeline intended to transform university course materials into detailed, coherent, source-faithful study notes. It combines lecture transcripts, professor-provided slides, personal notes, and relevant visual material while keeping the provenance of information traceable.

> **Status and accuracy:** NotesMaster currently provides scoped extraction, bounded generation, resumable checkpoints, local Ollama and explicitly approved Azure OpenAI profiles, deterministic image-link validation, and subject assembly. Semantic correctness and complete visual interpretation still require human review; `needs_review` is an explicit audit outcome.

## 1. Goals and constraints

The project is intended to:

  - Run locally on Windows 11, using Python and Ollama, without Docker.
- Prefer free, locally executable software and models.
- Support PDF, DOCX, Markdown, TXT, relevant images, audio, and video, subject to verified extractor capabilities.
- Handle large collections of material, with an earlier target of roughly 100–300 pages in total, by processing bounded units rather than sending everything to the model at once.
- Produce detailed, university-level notes rather than superficial summaries.
- Preserve definitions, explanations, reasoning, examples, formulas, code, diagrams, exceptions, qualifications, and other meaningful technical details.
- Keep every subject and section isolated.
- Reuse large source documents instead of repeatedly processing them in full.
- Preserve source provenance and page references wherever technically possible.
- Flag contradictions, gaps, unreadable pages, failed extraction, and uncertain coverage rather than guessing.
- Save progress, resume interrupted work, and reuse valid completed results.
- Produce one standalone `appunti_finali.md` for each section and a deterministic subject-level `appunti_materia.md`.
- Consider DOCX export and n8n orchestration only after the Python pipeline is reliable.

The output must not lose meaningful information merely to become shorter. Repetition may be removed only when it is genuinely redundant; distinct details, examples, exceptions, and qualifications must be retained.

## 2. Source hierarchy and fidelity

The agreed hierarchy is:

1. **Professor's lecture transcripts / sbobine:** primary source for explanations, reasoning, examples, and details discussed during the lesson.
2. **Professor's slides:** authoritative complementary source for official structure, terminology, definitions, formulas, diagrams, and figures.
3. **Personal notes:** supplementary material that must be considered, but is not authoritative when it conflicts with professor-provided sources.

When personal notes conflict with the professor's sources, do not present the conflicting personal claim as established course content. Preserve useful non-conflicting additions when appropriate. Record the conflict in a separate audit and, when material and supported by the supplied texts, add a concise English `> **Source clarification.** ...` note to the final study text; do not silently let personal notes override the professor.

When professor-provided sources conflict with each other, do not arbitrarily choose one, erase the disagreement, or invent a reconciliation. Preserve the distinct claims, identify their sources and page references where available, and flag the conflict for review.

Do not add external knowledge to fill gaps unless the user explicitly requests it. If external information is requested, clearly distinguish it from content supported by the course sources.

The goal is not to summarize sources independently and concatenate their summaries. The pipeline must eventually compare and integrate relevant information into coherent notes while preserving important content and traceability.

## 3. Intended architecture

Python controls deterministic workflow logic: directory discovery, path safety, source isolation, extraction orchestration, task creation, validation, checkpoints, retries, logging, dependency tracking, and deterministic assembly. The model performs bounded language tasks, such as explaining source material, describing relevant visual content, transcribing approved media, and consolidating a single section.

Ollama provides the initial local model runtime. The most recently reported model is `qwen3-vl:4b-instruct`; earlier work also referred to `qwen3-vl:4b` and `qwen3-vl:2b`. The target design is not tied to Ollama: every model-backed component will use an application-owned provider adapter selected by a named model profile. This allows a local runtime, an explicitly approved remote API, or another compatible backend to change without spreading provider-specific code through the pipeline.

Model selection uses named profiles such as `local_qwen` and `azure_gpt6_luna`. A profile resolves to an adapter kind, model identifier, runtime/endpoint reference, capabilities, and non-secret defaults. Remote execution requires the profile and `--allow-remote`; there is no silent provider fallback.

### Source-code layout

The repository keeps command-line entry points at the root for compatibility, while reusable business logic lives in the `appunti_ai/` package:

```text
appunti_ai/
├── contracts/      # Paths, subject/section discovery, configuration contracts
├── extraction/     # Source extraction and shared-source archive primitives
├── generation/     # Model-backed draft generation
├── consolidation/  # Section-level consolidation
├── diagnostics/    # Local Ollama/model diagnostics
└── modeling/       # Profiles, capability checks, and provider adapters
```

The `scripts/` directory contains CLI entry points, while reusable business logic belongs in `appunti_ai/`. `orchestrate.py` is the primary user-facing launcher. `model_profiles.json` contains non-secret profile definitions; credentials remain outside version control. Tests remain under `tests/` and must not depend on real course material or an active model runtime.

The primary interactive launcher is:

```powershell
python orchestrate.py
```

It asks for subject, section, and model/provider, then runs the complete pipeline. Advanced partial runs use the deterministic command:

```powershell
python scripts/run_pipeline.py --subject Reti
```

To prepare only one section, use `--section 02_NomeSezione`. A section-scoped run produces its own `appunti_finali.md`; subject assembly is deferred until all sections are selected. Drafts and merge caches are isolated under provider/profile-specific directories, so switching models does not require deleting previous work. Each phase writes separate state, audit, and diagnostic information.

For an isolated section, use for example:

```powershell
python scripts/generate_notes.py --extracted-dir output/Reti/01_Sezione/extracted --draft-dir output/Reti/01_Sezione/draft
python scripts/merge_notes.py --extracted-dir output/Reti/01_Sezione/extracted --draft-dir output/Reti/01_Sezione/draft --output-root output/Reti/01_Sezione
```

Before a model-backed run, validate the complete plan without contacting any provider:

```powershell
python scripts/run_pipeline.py --subject Reti --generate --consolidate --assemble --dry-run
```

After reviewing the dry-run, the same command without `--dry-run` executes the requested model-backed phases. `--generate`, `--consolidate`, and `--assemble` are independent opt-in phases; omitting them performs only deterministic preparation.

### Intended processing stages

1. Discover subjects and sections from the agreed directory structure.
2. Validate configuration, paths, file placement, and deterministic section order.
3. Extract text, page information, tables, relevant images, and, when supported, bounded audio/video-derived artifacts from section-specific sources.
4. Extract and index optional shared sources once per subject, preserving page-level traceability.
5. Retrieve only relevant shared-source passages for each section; do not repeatedly analyse the entire shared document.
6. Resolve an explicit model profile, preflight required capabilities, and generate bounded intermediate analyses from the correct sources.
7. Validate outputs and persist task state, dependency information, and diagnostics.
8. Consolidate each section into its own study-ready Markdown file.
9. Keep audits, source diagnostics, conflicts, and coverage limitations separate from the study text.
10. Assemble the subject-level Markdown document deterministically from validated section finals, without asking the model to rewrite it.
11. Consider DOCX export and n8n orchestration after the Python pipeline is dependable.

These are target stages. Do not assume that a stage is already implemented simply because it is documented here.

## 4. Target directory layout

The following is the agreed logical layout. Names of internal files and state schemas remain subject to inspection of the existing code before they become mandatory.

```text
Appunti/
├── README.md
├── AGENTS.md
├── .gitignore
├── .env.example
├── requirements.txt
├── extract_sources.py
├── generate_notes.py
├── merge_notes.py
├── run_pipeline.py                 # Planned orchestrator
├── input/
│   └── <Materia>/
│       ├── materia.json            # Proposed configuration; schema not final
│       ├── _condivise/             # Optional: sources shared by sections of this subject
│       ├── 01_<Sezione>/
│       │   └── <fonti specifiche>
│       ├── 02_<Sezione>/
│       │   └── <fonti specifiche>
│       └── ...
└── output/
    └── <Materia>/
        ├── _condivise/             # Only if input/_condivise/ exists
        │   ├── extracted/
        │   ├── pages/
        │   ├── index/
        │   └── shared_state.json
        ├── 01_<Sezione>/
        │   ├── extracted/
        │   ├── images/
        │   ├── draft/
        │   ├── audit/
        │   ├── state.json
        │   └── appunti_finali.md
        ├── 02_<Sezione>/
        │   └── ...
        ├── _archive/               # Source/extraction archive if required by implementation
        ├── appunti_materia.md
        ├── materia_state.json
        └── materia_log.txt
```

This layout is a target, not a reason to overwrite or migrate existing data blindly. Inspect current paths, output files, and state formats before modifying them.

### Subject and section discovery rules

- Each `input/<Materia>/` directory represents one independent subject.
- Each immediate subdirectory of `input/<Materia>/`, except `_condivise/`, represents one section. Do not discover sections recursively.
- Subdirectories inside a section belong to that section; they do not create additional sections.
- `_condivise/` is optional. It is only for sources genuinely shared by multiple sections of the same subject, for example a complete lecture transcript. A source relevant to only one section belongs in that section's folder.
- Shared sources are not automatically shared across subjects.
- Files placed directly in `input/<Materia>/`, other than the recognised subject configuration, must not be assigned to a section by guesswork. Report them and ask the user where they belong.
- Prefer numeric prefixes such as `01_` and `02_` for deterministic ordering. If ordering is ambiguous or prefixes collide, report the ambiguity and request a decision rather than inventing an order.
- Same-named files in different sections must not overwrite each other's extracted files, drafts, state, or final output.
- Outputs, drafts, and extracted text from one section must not become input sources for another section.
- `output/` is not a source for extraction or generation. The only planned exception is the subject assembler reading validated `appunti_finali.md` files from sections of that same subject.
- Never reuse sources, configuration, state, or outputs automatically across different subjects.

## 5. Outputs and their roles

### `appunti_finali.md`

A standalone, detailed, study-ready document for one section. It should be readable independently while remaining compatible in terminology and structure with the other sections of the same subject.

The style reference agreed for this project is `network.pdf`, previously described as being in the project root. Verify that the file is actually present and accessible before relying on it. It is a style reference, not an additional authoritative course source for unrelated subjects.

Final notes should have clear chapters and subchapters, connected explanatory prose, definitions, motivations, examples, formulas, exceptions, and important technical details. Use lists when they improve understanding, not as a substitute for explanation. Do not impose artificial headings where the source does not justify them.

Do not put model reasoning, prompt instructions, chunk metadata, internal processing commentary, or placeholders such as `[DA VERIFICARE]` into the study text. Put conflicts, gaps, and coverage limitations in `audit/` or separate metadata, except for the concise source clarification note explicitly allowed for material, source-supported conflicts.

### `appunti_materia.md`

A subject-level document assembled from validated section finals in a known order. Assembly must be deterministic and must not call the model to paraphrase, rewrite, shorten, or otherwise alter section content. It must record which section files and versions/hashes were used.

By default, do not publish a document as complete if a required section is missing or invalid. A partial assembly, if implemented, requires explicit user approval and must be clearly labelled partial.

### Intermediate material, audit, state, and logs

Keep source originals, extracted text, rendered pages/images, model drafts, state/checkpoints, logs, and audit reports separate from the final study text. A file's existence or non-zero size does not prove that its contents are valid.

## 6. Extraction and visual content

Extraction is distinct from semantic analysis. PDF and DOCX extraction may fail to recover all text, tables, layout, or visual information. Scanned PDFs may require OCR, but OCR has not been selected as a dependency and must not be assumed installed. A page with no extracted text may be a scan, diagram, or image; do not automatically treat it as blank.

Audio and video are first-class source modalities. Their original files remain authoritative evidence: a transcript, audio segment, or sampled frame is a derived artifact that must retain the media hash, source identity, and time-range provenance. Video frame selection and media transcription must be bounded; the pipeline must not submit an entire long recording or every frame in one request. Unsupported or failed media processing produces diagnostics and audit records, not a false claim of coverage. The authority of a source depends on who produced it, never on whether it is text, audio, or video.

Preserve original page numbers, table content, relevant images, and source references wherever technically possible. Resolve relative image paths correctly and verify that referenced image files exist before passing them to a model. Important visual information should remain traceable to the original page and should be flagged for review when extraction or visual interpretation is uncertain.

Supported formats are a project goal, not a guarantee that every element in every file can be extracted perfectly. Unsupported, unreadable, empty, or partially extracted inputs must produce explicit diagnostics.

## 7. Shared-source archive and retrieval

For a source in `input/<Materia>/_condivise/`, the target design is to extract and index it once, preserve page/chunk references, relevant images, metadata, and source hashes, and reuse those results across sections of the same subject.

For each section, retrieve only the passages likely to be relevant. Section-specific slides and notes can guide retrieval, but they may not cover the full transcript. Retrieval should use available titles, terminology, formulas, examples, and adjacent context; its coverage must be checked within finite limits.

The indexing method is not yet decided: lexical search, embeddings, or a hybrid approach remain options. Evaluate offline compatibility, quality, resource use, dependencies, and complexity before selecting one. Do not silently add a substantial dependency or treat a recommendation as user approval.

When a shared source changes, invalidate only dependent results where dependency tracking permits. If chunk-level dependencies are not yet available, it may be necessary to invalidate all sections of that subject that use the changed shared source, but not unrelated subjects.

## 8. Reliability, checkpoints, and resource constraints

The previously reported reference machine was Windows 11, Intel Core i7-1165G7, 8 GB RAM, NVIDIA MX450 with 2 GB VRAM, and Intel Iris Xe integrated graphics. These are historical reported specifications, not a live hardware check. Tune performance only after verifying the current machine, model, quantisation, Ollama version, and configuration.

Until tested otherwise, process model requests sequentially. Avoid loading all source documents into memory or sending very large documents in one request. Do not increase `num_ctx` or `num_predict` indiscriminately to compensate for poor segmentation.

The pipeline should:
- Validate useful content before marking a task complete.
- Detect empty, truncated, structurally invalid, and incomplete outputs.
- Use finite retries and avoid repeating identical requests after deterministic failures.
- Stop in a controlled way if Ollama is unavailable.
- Save outputs atomically where supported.
- Mark tasks complete only after output persistence and validation.
- Reuse valid completed work and invalidate results affected by changed inputs/configuration.
- Preserve valid prior outputs when an unrelated task fails.
- Record useful diagnostics such as model/configuration, duration, termination reason, attempts, and token counts when available. Clearly distinguish estimates from exact counts.
- Use explicit budgets for input size, output size, timeouts, retries, retrieval expansion, and merge depth.
- Ensure every loop has a guaranteed exit and measurable progress.
- Resolve model access through a named profile and capability-aware adapter, rather than direct SDK calls in pipeline components.
- Record non-secret profile/backend/model identity and required capabilities in task diagnostics and invalidation fingerprints.
- Require explicit opt-in before transferring any source material to a remote provider; no automatic local-to-cloud fallback is allowed.

Numeric limits and retry counts have not been finalised. Choose them after inspecting the actual runtime and code; ask for confirmation when the trade-off materially affects quality, memory, time, or dependencies.

## 9. Security and Git

Keep API keys, tokens, passwords, local `.env` files, virtual environments, caches, generated outputs, sensitive logs, and private course material out of version control unless there is a deliberate, appropriate reason to publish them. Course slides, recordings, transcripts, personal notes, and generated notes may be copyrighted or otherwise unsuitable for a public repository.

`.gitignore` does not remove files already tracked by Git and does not erase secrets from repository history. Review tracked files before publishing. A `.env.example` should contain variable names and placeholder/empty values only, never real secrets. Local Ollama generally does not require a third-party API key, but verify the actual integration before assuming no credentials are used. Remote provider credentials and raw HTTP authorization data must never enter `materia.json`, state, manifests, prompts, logs, or audit reports.

Be careful with broad ignore patterns such as `*.pdf`: they also ignore a style reference such as `network.pdf`. Decide deliberately whether that file is appropriate to track; do not publish it merely to make the example work.

## 10. Current status and roadmap

A previous version of the project included `extract_sources.py`, `generate_notes.py`, and `merge_notes.py`; `run_pipeline.py` is planned. The exact contents of the current local scripts have not been verified in this document.

A previously observed defect was that some `output/draft/` files contained metadata but almost no analysis even though `state.json` marked the tasks complete. The immediate cause was not proven. The next diagnosis should inspect the actual response returned by Ollama, then validate output content and fix resumption logic so invalid files are not accepted as completed.

Recommended implementation order:

1. Inspect actual scripts, configuration, directory tree, state JSON, dependencies, and sample outputs. Do not modify files during this initial audit.
2. Define and test subject/section discovery, path safety, ordering, and handling of misplaced files.
3. Implement isolated extraction for section-specific and optional shared sources.
4. Design the shared-source archive and choose retrieval only after evaluating trade-offs.
5. Implement the provider adapter boundary, explicit model-profile selection, capability preflight, and remote-transfer safeguards; then implement bounded generation, output validation, atomic checkpoints, and finite retries.
6. Consolidate each section independently and keep audit information separate.
7. Implement deterministic subject assembly without model rewriting.
8. Add `run_pipeline.py` only after component behaviour and tests are reliable.
9. Evaluate DOCX export and n8n after the Python pipeline is stable.

Make small, reversible changes and verify each phase before proceeding. n8n is a future orchestrator, not a prerequisite for testing the Python pipeline.

## 11. Known decisions versus open decisions

Agreed: local-first processing; no Docker; source hierarchy; no invented facts; section and subject isolation; optional shared sources; per-section final Markdown; deterministic subject assembly; finite retries and loops; checkpoint/resumption; separate audits; inspect the real code before modifying it; delay n8n until Python is reliable.

Still open and not to be guessed: shared-source indexing strategy; whether/how to add OCR; audio/video decoder, transcription, diarization, and frame-sampling strategy; initial provider adapters; model-profile file format and scope; remote-provider approval path; final numeric budgets and timeouts; final state schema and migration strategy; behaviour for missing/duplicate numeric section prefixes; treatment of headings and internal references during assembly; whether partial assembly is allowed; measurable coverage criteria and required manual review; migration of existing outputs; DOCX export details.

## 12. Definition of done

The pipeline should not be called reliable until:
- Subjects and sections are discovered without ambiguity.
- Sources and outputs do not leak between subjects or sections.
- Unchanged shared sources are reused.
- Original sources, pages, and relevant images remain traceable.
- Each section produces standalone, readable, detailed study notes.
- Contradictions and gaps are reported without invented reconciliation.
- Audit, logs, drafts, and state are separate from the study text.
- All loops and retries have finite limits.
- Empty/truncated outputs are not marked complete.
- Audio/video processing, when enabled, preserves temporal provenance; unsupported media is reported rather than silently skipped.
- Model/profile changes are traceable and do not require edits across pipeline components.
- No source material is transferred to a remote provider without explicit approval.
- A second run reuses valid results.
- Changes invalidate only relevant dependencies.
- Subject assembly is deterministic and does not call the model.
- Incomplete assembly is not presented as complete.
- Failure in one section does not delete valid results from another.
- Isolation, resumption, error, resource-limit, and assembly tests have actually been run.
- Remaining limitations are documented.

For operational rules and implementation requirements, see `AGENTS.md`.
