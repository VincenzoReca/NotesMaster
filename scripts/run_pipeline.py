from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
import argparse
import json
import subprocess
import sys
sys.path.insert(0, str(PROJECT_ROOT))
from datetime import datetime


def progress(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)

from appunti_ai.orchestration.pipeline import run_preparation
from appunti_ai.contracts.paths import ensure_within
from appunti_ai.contracts.paths import discover_subject_names



def main():
    parser = argparse.ArgumentParser(description="Coordina discovery, estrazione e assembly deterministici.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--subject")
    parser.add_argument("--all", action="store_true", help="Esegue la pipeline per tutte le materie immediate in input/.")
    parser.add_argument("--section", help="Prepara una sola sezione esplicitamente.")
    parser.add_argument("--no-extract", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    parser.add_argument("--generate", action="store_true", help="Genera i draft per ogni sezione.")
    parser.add_argument("--consolidate", action="store_true", help="Consolida ogni sezione dopo la generazione.")
    parser.add_argument("--model-profile", help="Profilo modello da inoltrare alle fasi model-backed.")
    parser.add_argument("--profiles", type=Path, help="File profili da inoltrare alle fasi model-backed.")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Mostra le fasi senza eseguirle.")
    parser.add_argument("--json-only", action="store_true", help="Emette su stdout solo il report JSON finale per script e automazioni.")
    parser.add_argument("--report-path", type=Path, help="Percorso del report JSON; per default output/<Materia>/pipeline_report.json.")
    args = parser.parse_args()
    if (args.subject is None and not args.all) or (args.subject is not None and args.all):
        parser.error("Specificare --subject <Materia> oppure --all, ma non entrambi.")
    if args.all:
        try:
            subjects, issues = discover_subject_names(args.project_root.resolve())
            reports = []
            for subject in subjects:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Batch subject {subject}: starting", file=sys.stderr, flush=True)
                command = [sys.executable, str(Path(__file__).resolve()), "--project-root", str(args.project_root.resolve()), "--subject", subject]
                for flag in ("no_extract", "assemble", "generate", "consolidate", "allow_remote", "dry_run"):
                    if getattr(args, flag):
                        command.append("--" + flag.replace("_", "-"))
                if args.section:
                    command.extend(["--section", args.section])
                if args.model_profile:
                    command.extend(["--model-profile", args.model_profile])
                if args.profiles:
                    command.extend(["--profiles", str(args.profiles)])
                command.append("--json-only")
                result = subprocess.run(command, cwd=args.project_root, check=False, capture_output=True, text=True)
                try:
                    raw_report = result.stdout.strip()
                    subject_report = json.loads(raw_report[raw_report.find("{"):])
                except json.JSONDecodeError:
                    subject_report = {"subject": subject, "status": "failed", "error": result.stderr.strip() or result.stdout.strip()}
                subject_report.setdefault("status", "completed" if result.returncode == 0 else "failed")
                if isinstance(subject_report.get("assembly"), dict) and subject_report["assembly"].get("status") == "needs_review":
                    subject_report["status"] = "needs_review"
                reports.append(subject_report)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Batch subject {subject}: {subject_report.get('status')}", file=sys.stderr, flush=True)
            batch = {"status": "completed" if reports and all(item.get("status") == "completed" for item in reports) else "needs_review", "subjects": reports, "discovery_issues": [str(issue.path) + ": " + issue.message for issue in issues]}
            batch_path = (args.project_root.resolve() / "output" / "pipeline_batch_report.json")
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            batch["report_path"] = str(batch_path)
            print(json.dumps(batch, ensure_ascii=False, indent=2))
            return
        except Exception as exc:
            parser.error(f"batch pipeline arrestata: {type(exc).__name__}: {exc}")
    try:
        progress(f"Starting subject {args.subject or 'batch'}", quiet=args.json_only)
        report = run_preparation(args.project_root, args.subject, section_name=args.section, extract=not args.no_extract, assemble=False)
        progress(f"Discovered {len(report['sections'])} section(s)", quiet=args.json_only)
        if args.generate or args.consolidate:
            subject_output = args.project_root.resolve() / "output" / args.subject
            statuses = {}
            selected = report["sections"]
            for section_name in selected:
                progress(f"[{args.subject}] Section {section_name}: starting model phases", quiet=args.json_only)
                section_output = subject_output / section_name
                profile_dir = (args.model_profile or "local_qwen").replace("..", "_").replace("/", "_").replace("\\", "_")
                draft_output = section_output / "draft" / profile_dir
                # Preserve resumability of pre-profile legacy local drafts;
                # every new provider/profile gets its own isolated directory.
                legacy_draft = section_output / "draft"
                if profile_dir == "local_qwen" and not draft_output.exists() and (legacy_draft / "state.json").is_file():
                    draft_output = legacy_draft
                commands = []
                if args.generate:
                    commands.append([sys.executable, str(PROJECT_ROOT / "scripts" / "generate_notes.py"), "--extracted-dir", str(section_output / "extracted"), "--draft-dir", str(draft_output)])
                if args.consolidate:
                    commands.append([sys.executable, str(PROJECT_ROOT / "scripts" / "merge_notes.py"), "--extracted-dir", str(section_output / "extracted"), "--draft-dir", str(draft_output), "--output-root", str(section_output), "--cache-dir", str(section_output / "audit" / "merge_cache" / profile_dir)])
                for command in commands:
                    progress(f"[{args.subject}/{section_name}] Running {command[1]}", quiet=args.json_only)
                    if command[1] == "generate_notes.py" and (subject_output / "_condivise").is_dir():
                        command.extend(["--shared-output", str(subject_output / "_condivise")])
                    if args.model_profile:
                        command.extend(["--model-profile", args.model_profile])
                    if args.profiles:
                        command.extend(["--profiles", str(args.profiles)])
                    if args.allow_remote:
                        command.append("--allow-remote")
                if args.dry_run:
                    statuses[section_name] = [{"command": command, "returncode": None, "status": "planned"} for command in commands]
                    continue
                section_status = []
                for command in commands:
                    started_at = datetime.now()
                    result = subprocess.run(command, cwd=args.project_root, check=False, capture_output=True, text=True)
                    if result.stdout and not args.json_only:
                        print(result.stdout, end="")
                    if result.stderr and not args.json_only:
                        print(result.stderr, end="")
                    diagnostic = (result.stdout + "\n" + result.stderr).casefold()
                    status = "completed" if result.returncode == 0 else ("needs_review" if any(marker in diagnostic for marker in ("needs_review", "truncated", "coverage", "validation")) else "failed")
                    section_status.append({"command": command[1], "returncode": result.returncode, "status": status, "duration_seconds": round((datetime.now() - started_at).total_seconds(), 2), "diagnostic_tail": (result.stderr or result.stdout or "").strip()[-1000:]})
                    progress(f"[{args.subject}/{section_name}] {command[1]} -> {status}", quiet=args.json_only)
                    if result.returncode != 0:
                        break
                statuses[section_name] = section_status
            report["model_phases"] = statuses
            report["failed_model_sections"] = [
                name for name, entries in statuses.items()
                if any(entry.get("returncode") not in (None, 0) for entry in entries)
            ]
        if args.assemble:
            if args.section:
                # A section-scoped run must remain useful on its own. The
                # subject assembler requires every section, so defer it until
                # an explicit all-sections run.
                section_final = (args.project_root.resolve() / "output" / args.subject / args.section / "appunti_finali.md")
                report["assembly"] = {
                    "status": "section_only",
                    "section": args.section,
                    "output": str(section_final),
                    "valid": section_final.is_file(),
                    "message": "Subject assembly deferred; this run produces the selected section final only.",
                }
                progress(f"[{args.subject}/{args.section}] Section assembly complete; subject assembly deferred", quiet=args.json_only)
            else:
                progress(f"[{args.subject}] Assembling validated section finals", quiet=args.json_only)
                try:
                    assembly_report = run_preparation(args.project_root, args.subject, section_name=None, extract=False, assemble=True)
                    report["assembly"] = assembly_report.get("assembly")
                except Exception as exc:
                    # Preserve the detailed per-section/model report instead of converting
                    # a validly blocked incomplete assembly into a generic CLI error.
                    report["assembly"] = {
                        "status": "needs_review",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
        output_root = (args.project_root.resolve() / "output" / args.subject).resolve()
        report_path = ensure_within(
            (args.report_path or (output_root / "pipeline_report.json")).resolve(),
            output_root,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(report_path)
        temporary = report_path.with_suffix(report_path.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report_path)
    except Exception as exc:
        parser.error(f"pipeline arrestata: {type(exc).__name__}: {exc}")
    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"subject": report["subject"], "sections": report["sections"], "phases": [key for key in report if key not in {"subject", "sections"}]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
