"""
Manages the OS-level read-only lock on supply_chain.db.

This exists because the security model's filesystem layer (read-only
at the OS level) is fundamentally incompatible with the build phase
(generate_data.py writing seed data, dbt run creating mart views) --
both need write access. Rather than relying on manually remembering
the correct attrib/chmod commands in the correct order every time the
database needs rebuilding, this script makes that lifecycle explicit
and scriptable.

Usage:
    python manage_lock.py unlock   # before generate_data.py / dbt run
    python manage_lock.py lock     # after dbt run, before runtime use
"""

import os
import platform
import subprocess
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supply_chain.db")


def unlock():
    if not os.path.exists(DB_PATH):
        print(f"{DB_PATH} does not exist yet -- nothing to unlock.")
        return
    if platform.system() == "Windows":
        subprocess.run(["attrib", "-r", DB_PATH], check=True)
    else:
        os.chmod(DB_PATH, 0o644)
    print(f"Unlocked (write-enabled): {DB_PATH}")


def lock():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} does not exist. Run generate_data.py and dbt run first.")
        sys.exit(1)
    if platform.system() == "Windows":
        subprocess.run(["attrib", "+r", DB_PATH], check=True)
    else:
        os.chmod(DB_PATH, 0o444)
    print(f"Locked (read-only): {DB_PATH}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("lock", "unlock"):
        print("Usage: python manage_lock.py [lock|unlock]")
        sys.exit(1)
    if sys.argv[1] == "lock":
        lock()
    else:
        unlock()