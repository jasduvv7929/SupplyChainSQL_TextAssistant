"""
One-command build orchestrator for the supply chain database and dbt
marts.

Runs, in order:
  1. Unlock the database file (in case it's locked from a previous run)
  2. Generate the seed data (db/generate_data.py)
  3. Build the dbt mart views (dbt run, inside supply_chain_dbt/)
  4. Lock the database file read-only (the filesystem layer of the
     security model -- this must happen LAST, since both steps 2 and 3
     need write access)

This exists because the read-only filesystem lock (a deliberate
security layer) is fundamentally incompatible with the build steps
that create the data and views it's meant to protect afterward.
Rather than relying on remembering the correct order of standalone
commands, this script makes the full lifecycle a single, repeatable
command: `python build.py`.

Safe to re-run: if the database already exists and is locked, it
unlocks first; if it doesn't exist yet, generation proceeds normally.
"""

import subprocess
import sys
from pathlib import Path
import shutil

ROOT = Path(__file__).parent.resolve()
DB_DIR = ROOT / "db"
DBT_DIR = ROOT / "supply_chain_dbt"
dbt_path = shutil.which("dbt") or str(Path(sys.executable).parent / "dbt")

def run(cmd, cwd, step_name):
    print(f"\n--- {step_name} ---")
    print(f"$ {' '.join(cmd)}  (in {cwd})")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"\nFAILED at step: {step_name} (exit code {result.returncode})")
        sys.exit(result.returncode)


def main():
    run([sys.executable, "manage_lock.py", "unlock"], cwd=DB_DIR, step_name="1. Unlock database file")
    run([sys.executable, "generate_data.py"], cwd=DB_DIR, step_name="2. Generate seed data")
    run([dbt_path, "run", "--profiles-dir", "."], cwd=DBT_DIR, step_name="3. Build dbt mart views")
    run([sys.executable, "manage_lock.py", "lock"], cwd=DB_DIR, step_name="4. Lock database file (read-only)")
    print("\nBuild complete. Database is generated, dbt marts are built, and the file is locked read-only.")
    print("To rebuild from scratch, just run `python build.py` again.")


if __name__ == "__main__":
    main()