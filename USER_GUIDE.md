# AppuntiAI User Guide

AppuntiAI turns organised course materials into source-faithful Markdown notes. Python owns discovery, extraction, model calls, validation, checkpoints, and deterministic assembly.

## 1. Requirements

- Windows with Python and the project virtual environment.
- The dependencies installed in `.venv`.
- Ollama running locally for the default profile, with the configured model available.

Always run commands from the repository root:

```powershell
cd <project-root>
```

Use the virtual-environment interpreter explicitly on Windows:

```powershell
.\.venv\Scripts\python.exe
```

## 2. Organise input files

Create one directory per subject and one immediate, numerically prefixed directory per section. `materia.json` is optional for ordinary runs; the defaults are inferred from the directory contract.

```text
input/
└── Networks/
    ├── materia.json              # optional overrides
    ├── _condivise/
    │   └── lecture_transcript.txt
    ├── 01_Introduction/
    │   └── personal_notes.docx
    └── 02_Routing/
        ├── lecture.md
        └── slides.pdf
```

Use `01_`, `02_`, `03_`, ... to define the exact order. Files placed directly in a subject directory are reported and are not assigned automatically. The `output/` directory is never used as input.

The source hierarchy is:

1. professor lecture transcripts;
2. professor slides;
3. personal notes.

Conflicting personal-note claims are not treated as established course content. Material conflicts are recorded in the audit and may receive a concise `Source clarification` note in the final English text.

## 3. Inspect the structure

List subjects:

```powershell
.\.venv\Scripts\python.exe pipeline_paths.py --project-root .
```

Inspect one subject and its ordered sections:

```powershell
.\.venv\Scripts\python.exe pipeline_paths.py --project-root . --subject Networks
```

## 4. Run the pipeline with Python

For an interactive terminal launcher that asks only for the project, scope, phases, and model profile, use:

```powershell
.\.venv\Scripts\python.exe orchestrate.py
```

The launcher is a convenience wrapper over `scripts/run_pipeline.py`; it does not duplicate pipeline logic.

Preparation and extraction only:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py --subject Networks
```

Full run for one subject:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py `
  --subject Networks `
  --generate `
  --consolidate `
  --assemble
```

Process every subject under `input/`:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py `
  --all `
  --generate `
  --consolidate `
  --assemble
```

Validate the planned work without contacting a model:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py --subject Networks --generate --consolidate --assemble --dry-run
```

For automation, request machine-readable output:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py --subject Networks --generate --consolidate --assemble --json-only
```

## 5. Outputs

For each subject, results are written under:

```text
output/Networks/
├── 01_Introduction/
│   ├── extracted/
│   ├── images/
│   ├── draft/
│   ├── audit/
│   └── appunti_finali.md
├── 02_Routing/
│   └── ...
├── appunti_materia.md
├── materia_state.json
└── pipeline_report.json
```

The final subject document is `output/<Subject>/appunti_materia.md`. It is assembled deterministically from validated section finals and is not rewritten by a model. For `--all`, the aggregate report is `output/pipeline_batch_report.json`.

Audits and state files contain extraction diagnostics, validation outcomes, conflicts, coverage information, hashes, and resumability metadata. They are separate from the study text.

## 6. Change the model

Model selection is profile-based. Profiles are stored in `model_profiles.json` and the selected name is passed explicitly:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py --subject Networks --generate --consolidate --model-profile local_qwen
```

The default profile currently uses Ollama and a vision-capable Qwen model. A profile includes:

- `backend`;
- `model`;
- required `capabilities` such as `text` and `vision`;
- non-secret request `options`;
- remote-provider fields when applicable.

To switch to another local Ollama model, duplicate the profile and change only its name and model identifier, for example:

```json
{
  "version": 1,
  "default_profile": "local_qwen_alt",
  "profiles": {
    "local_qwen_alt": {
      "backend": "ollama",
      "model": "your-local-model:tag",
      "capabilities": ["text", "vision"],
      "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 2500},
      "remote": false
    }
  }
}
```

The model must actually support every advertised capability. The pipeline performs capability checks and fails in a controlled way when a capability is missing.

### Remote providers

Remote execution is opt-in. Use a profile with `backend` set to `openai_compatible` or `azure_openai`, keep credentials in environment variables, and pass `--allow-remote` explicitly. Never put API keys in `model_profiles.json`, input files, state, logs, or Git.

An Azure profile additionally requires `deployment`, `api_version`, an endpoint or endpoint environment variable, and an API-key environment variable. Example shape (with placeholders only):

```json
"azure_course_model": {
  "backend": "azure_openai",
  "model": "provider-model-name",
  "deployment": "deployment-name",
  "api_version": "2024-xx-xx",
  "endpoint_env": "AZURE_OPENAI_ENDPOINT",
  "api_key_env": "AZURE_OPENAI_API_KEY",
  "capabilities": ["text", "vision"],
  "options": {},
  "remote": true
}
```

Use the provider's actual API version and deployment values. Remote transfer is never implicit.

## 7. Use the interactive Python orchestrator

For the simplest terminal workflow, run:

```powershell
.\.venv\Scripts\python.exe orchestrate.py
```

Type `all` to process every subject, or `subject` followed by one subject folder name. The launcher runs extraction, generation, consolidation, and assembly and then returns the Python pipeline report.

## 8. Resume and rerun

Rerunning an unchanged command reuses valid extracted and model-backed results when their hashes and configuration fingerprints still match. Changing a source, profile, or relevant prompt invalidates affected work. Valid results from unrelated sections are preserved after a failure.

Do not delete `output/` state files to solve an error. Read the relevant audit and report first.

## 9. Troubleshooting

- **Model unavailable:** start Ollama and verify that the model named by the selected profile is installed.
- **Unknown profile:** check the profile name and JSON schema in `model_profiles.json`.
- **Missing capability:** use a model/profile advertising the required capability; do not rely on silent fallback.
- **Section discovery error:** rename section directories with unique numeric prefixes.
- **Assembly blocked:** inspect each section's `appunti_finali.md` and `audit/` directory; complete assembly requires every required section to be valid.
- **Unsupported audio/video/OCR:** the original source is retained and the limitation is reported. These capabilities are not silently treated as successful extraction.

## 10. Current scope

The reliable end-to-end path currently covers PDF, DOCX, Markdown, TXT, and preserved/analysed images subject to extractor and model capabilities. Audio/video ingestion and dedicated OCR remain bounded future extensions.
