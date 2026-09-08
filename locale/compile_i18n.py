"""Compile i18n: Python sources → .ts → .qm

Usage:
    python compile_i18n.py

Requires:
    pylupdate5 (shipped with PySide6)
    lrelease  (from Qt, e.g. 'pip install aqtinstall' or Qt SDK)
"""
import subprocess
import sys
from pathlib import Path


def main():
    locale_dir = Path(__file__).resolve().parent
    pro_file = locale_dir / "polyfoammesh.pro"

    if not pro_file.exists():
        print(f"[ERROR] Project file not found: {pro_file}")
        sys.exit(1)

    # Step 1: pylupdate5 — scan Python sources and generate/update .ts
    print(f"[i18n] pylupdate5: scanning sources via {pro_file.name}...")
    result = subprocess.run(
        ["pylupdate5", str(pro_file)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] pylupdate5 failed:\n{result.stderr}")
        sys.exit(1)
    print("[i18n] .ts file updated.")

    # Step 2: lrelease — convert .ts to .qm
    print("[i18n] lrelease: compiling .ts → .qm...")
    result = subprocess.run(
        ["lrelease", str(pro_file)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] lrelease failed:\n{result.stderr}")
        sys.exit(1)

    # Report
    qm_files = list(locale_dir.rglob("*.qm"))
    if qm_files:
        print(f"[i18n] Done. {len(qm_files)} .qm file(s) generated:")
        for f in qm_files:
            print(f"       {f.relative_to(locale_dir)}")
    else:
        print("[WARN] No .qm files generated. Check .pro TRANSLATIONS path.")
        sys.exit(1)


if __name__ == "__main__":
    main()
