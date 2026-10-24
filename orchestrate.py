"""Interactive local launcher for AppuntiAI.

This is a convenience layer over run_pipeline.py. It does not implement
discovery, extraction, model calls, validation, or assembly itself.
"""

from pathlib import Path
import subprocess
import sys


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def main() -> int:
    root = Path(__file__).resolve().parent
    project_root = root
    print(f"AppuntiAI project: {project_root}")
    mode = ask("What do you want to process? Type 'all' for every subject, or 'subject' for one", "all").casefold()
    command = [sys.executable, str(root / "scripts" / "run_pipeline.py"), "--project-root", str(project_root)]
    if mode in {"all", "all subjects", "tutte", "tutti"}:
        command.append("--all")
    else:
        subject = ask("Type the subject folder name")
        if not subject:
            print("A subject name is required.", file=sys.stderr)
            return 2
        command.extend(["--subject", subject])
    command.extend(["--generate", "--consolidate", "--assemble"])
    print("\nStarting the complete pipeline (extract, generate, consolidate, assemble).")
    print("The process may take time while the local model is working. Progress will be shown below.\n")
    result = subprocess.run(command, cwd=project_root, check=False)
    print(f"\nPipeline finished with exit code {result.returncode}.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
