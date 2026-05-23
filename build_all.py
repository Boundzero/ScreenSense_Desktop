"""Build orchestrator for ScreenSense.

Builds in two stages:
  1. The main ScreenSense.exe (the actual app users will run from USB)
  2. The ScreenSenseInstaller.exe (bundles the main app as a payload)

Run from project root:
    python build_all.py

After running you'll have:
    dist/ScreenSense.exe              ← the portable app (copy to USB)
    dist/ScreenSenseInstaller.exe     ← the installer (ship this to users)

The installer is the only thing your users need. They double-click it,
walk through the wizard, and end up with a fully configured USB drive.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run(cmd: list[str], **kw) -> None:
    print(f"\n→ {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, **kw)
    if result.returncode != 0:
        print(f"\n✗ Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def clean() -> None:
    print("Cleaning previous builds…")
    for d in ("build", "dist", "__pycache__"):
        p = PROJECT_ROOT / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def check_prereqs() -> None:
    """Make sure PyInstaller is available."""
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("✗ PyInstaller not found. Install with: pip install pyinstaller")
        sys.exit(1)


def build_main_app() -> None:
    print("\n" + "=" * 60)
    print("STAGE 1: Building ScreenSense.exe (main app)")
    print("=" * 60)
    run([sys.executable, "-m", "PyInstaller", "ScreenSense.spec", "--clean", "--noconfirm"])

    exe = PROJECT_ROOT / "dist" / "ScreenSense.exe"
    if not exe.exists():
        print(f"✗ Expected {exe} but it wasn't built. Aborting.")
        sys.exit(1)
    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"\n✓ Built dist/ScreenSense.exe  ({size_mb:.1f} MB)")


def build_installer() -> None:
    print("\n" + "=" * 60)
    print("STAGE 2: Building ScreenSenseInstaller.exe")
    print("=" * 60)
    # Don't pass --clean here or it'd wipe the main app we just built.
    run([sys.executable, "-m", "PyInstaller", "ScreenSenseInstaller.spec", "--noconfirm"])

    exe = PROJECT_ROOT / "dist" / "ScreenSenseInstaller.exe"
    if not exe.exists():
        print(f"✗ Expected {exe} but it wasn't built. Aborting.")
        sys.exit(1)
    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"\n✓ Built dist/ScreenSenseInstaller.exe  ({size_mb:.1f} MB)")


def summary() -> None:
    print("\n" + "=" * 60)
    print("BUILD COMPLETE")
    print("=" * 60)
    dist = PROJECT_ROOT / "dist"
    for f in sorted(dist.glob("*.exe")):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"  {f.name}    {size_mb:.1f} MB")
    print(
        "\nNext steps:\n"
        "  • Ship dist/ScreenSenseInstaller.exe to your users.\n"
        "  • They double-click it, walk through the wizard, and end up\n"
        "    with a configured USB drive.\n"
        "  • dist/ScreenSense.exe is the bare app — you only need to ship\n"
        "    this if you're distributing pre-loaded USB sticks yourself.\n"
    )


def main() -> None:
    check_prereqs()
    if "--clean" in sys.argv:
        clean()
    build_main_app()
    build_installer()
    summary()


if __name__ == "__main__":
    main()
