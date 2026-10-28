"""Interactive local launcher for NotesMaster.

This is a convenience layer over run_pipeline.py. It does not implement
discovery, extraction, model calls, validation, or assembly itself.
"""

from pathlib import Path
import subprocess
import sys

from appunti_ai.contracts.paths import discover_subject, discover_subject_names
from appunti_ai.modeling.profiles import ProfileError, load_profiles


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def choose_subject(project_root: Path) -> tuple[str | None, bool] | None:
    subjects, issues = discover_subject_names(project_root)
    if issues:
        print("Discovery warnings:")
        for issue in issues:
            print(f"  - {issue.path}: {issue.message}")
    if not subjects:
        print("No subject folders were found in input/.", file=sys.stderr)
        return None
    print("\nChoose what to process:")
    print("  0. All subjects")
    for index, subject in enumerate(subjects, start=1):
        print(f"  {index}. {subject}")
    while True:
        choice = ask("Subject number", "0")
        if choice == "0":
            return None, True
        if choice.isdigit() and 1 <= int(choice) <= len(subjects):
            return subjects[int(choice) - 1], False
        print("Invalid choice. Select one of the displayed numbers.")


def choose_profile(profile_path: Path) -> tuple[str, bool] | None:
    try:
        default_name, profiles = load_profiles(profile_path)
    except ProfileError as exc:
        print(f"Cannot load model profiles: {exc}", file=sys.stderr)
        return None
    names = list(profiles)
    print("\nChoose the model profile:")
    for index, name in enumerate(names, start=1):
        profile = profiles[name]
        remote = "remote" if profile.remote else "local"
        marker = " (default)" if name == default_name else ""
        print(f"  {index}. {name} [{profile.model}, {remote}]{marker}")
    while True:
        default_index = str(names.index(default_name) + 1)
        choice = ask("Model number", default_index)
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            selected = profiles[names[int(choice) - 1]]
            return selected.name, selected.remote
        print("Invalid choice. Select one of the displayed numbers.")


def choose_section(project_root: Path, subject: str) -> str | None:
    try:
        sections = discover_subject(project_root, subject).sections
    except Exception as exc:
        print(f"Cannot inspect sections for {subject}: {exc}", file=sys.stderr)
        return None
    print(f"\nChoose what to analyze in {subject}:")
    print("  0. All sections")
    for index, section in enumerate(sections, start=1):
        print(f"  {index}. {section.name}")
    while True:
        choice = ask("Section number", "0")
        if choice == "0":
            return ""
        if choice.isdigit() and 1 <= int(choice) <= len(sections):
            return sections[int(choice) - 1].name
        print("Invalid choice. Select one of the displayed numbers.")


def main() -> int:
    root = Path(__file__).resolve().parent
    project_root = root
    print(f"NotesMaster project: {project_root}")
    subject_choice = choose_subject(project_root)
    if subject_choice is None:
        return 2
    subject, all_subjects = subject_choice
    section = None
    if not all_subjects:
        section = choose_section(project_root, subject)
        if section is None:
            return 2
    profile_choice = choose_profile(project_root / "model_profiles.json")
    if profile_choice is None:
        return 2
    model_profile, is_remote = profile_choice
    command = [sys.executable, str(root / "scripts" / "run_pipeline.py"), "--project-root", str(project_root)]
    if all_subjects:
        command.append("--all")
    else:
        command.extend(["--subject", subject])
        if section:
            command.extend(["--section", section])
    command.extend(["--generate", "--consolidate", "--assemble"])
    command.extend(["--model-profile", model_profile])
    if is_remote:
        command.append("--allow-remote")
    print("\nStarting the complete pipeline (extract, generate, consolidate, assemble).")
    print("The process may take time while the local model is working. Progress will be shown below.\n")
    result = subprocess.run(command, cwd=project_root, check=False)
    print(f"\nPipeline finished with exit code {result.returncode}.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
