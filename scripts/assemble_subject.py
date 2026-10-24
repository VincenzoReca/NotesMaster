from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appunti_ai.assembly.subject import AssemblyError, assemble_subject
from appunti_ai.contracts.paths import PathContractError, discover_subject


def main():
    parser = argparse.ArgumentParser(description="Assembla le sezioni validate di una materia senza usare il modello.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    try:
        state = assemble_subject(discover_subject(args.project_root, args.subject))
    except (AssemblyError, PathContractError) as exc:
        parser.error(str(exc))
    print(f"Documento creato: {state['output']}")


if __name__ == "__main__":
    main()
