"""
Environment bootstrap — makes "just give it a path" work even when the target
needs its own dependencies.

A project's code usually only imports in its own virtualenv (jeevn needs
rasterio/GDAL, etc.). Since the tester runs the target IN-PROCESS to trace its
steps, it must run inside that venv. This module:
  * finds the project's interpreter (``.venv`` etc.),
  * ensures the tester's own deps (google-genai, python-dotenv) exist there,
  * re-executes the CLI using the project's interpreter, with the tester's
    ``src`` on PYTHONPATH and the API key forwarded.

Guarded by ``AUTO_TESTER_BOOTSTRAPPED`` so it only hops once.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

_VENV_SUBPATHS = [
    Path("Scripts") / "python.exe",   # Windows
    Path("bin") / "python",            # POSIX
]
_VENV_DIRS = [".venv", "venv", "env", ".venv312", ".env"]
_TESTER_SRC = str(Path(__file__).resolve().parents[1])  # …/src


def find_project_python(root: str | Path) -> Optional[str]:
    """Return the path to a virtualenv interpreter inside the project, if any."""
    root = Path(root)
    for d in _VENV_DIRS:
        for sub in _VENV_SUBPATHS:
            cand = root / d / sub
            if cand.exists():
                # sanity: must have the project's deps, not be an empty venv.
                return str(cand)
    return None


def _same_interpreter(py: str) -> bool:
    try:
        return Path(py).resolve() == Path(sys.executable).resolve()
    except Exception:
        return False


def ensure_tester_deps(python: str) -> None:
    """Install the tester's runtime deps into the project's interpreter (idempotent)."""
    check = subprocess.run(
        [python, "-c", "import google.genai, dotenv"],
        capture_output=True, text=True,
    )
    if check.returncode == 0:
        return
    print(f"[bootstrap] installing tester deps into {python} …")
    subprocess.run(
        [python, "-m", "pip", "install", "--quiet", "google-genai", "python-dotenv"],
        check=False,
    )


def reexec_in_project_env(python: str, argv: List[str]) -> int:
    """Re-run the CLI under the project's interpreter; return its exit code."""
    env = dict(os.environ)
    env["AUTO_TESTER_BOOTSTRAPPED"] = "1"
    # tester src on path so `python -m auto_tester.cli` resolves there
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _TESTER_SRC + (os.pathsep + existing if existing else "")
    cmd = [python, "-m", "auto_tester.cli"] + argv
    print(f"[bootstrap] running in project env: {python}")
    return subprocess.run(cmd, env=env).returncode


def maybe_bootstrap(project_root: str | Path, argv: List[str],
                    explicit_python: Optional[str] = None) -> Optional[int]:
    """If the project needs its own env and we're not already in it, hop into it.

    Returns the child exit code if it re-executed, else ``None`` (caller proceeds
    in the current interpreter).
    """
    if os.environ.get("AUTO_TESTER_BOOTSTRAPPED"):
        return None
    python = explicit_python or find_project_python(project_root)
    if not python or _same_interpreter(python):
        return None
    ensure_tester_deps(python)
    return reexec_in_project_env(python, argv)
