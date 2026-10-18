"""Zero-dependency bootstrap used by run.bat and run.sh."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if sys.version_info < (3, 11):
        print("Espresso needs Python 3.11 or newer (3.12 recommended). Install it and run again.")
        return 1
    os.chdir(ROOT)
    env_dir = ROOT / ".venv"
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating Espresso's private Python environment...", flush=True)
        try:
            venv.EnvBuilder(with_pip=True).create(env_dir)
        except Exception:
            print("Could not create .venv. On Debian/Ubuntu, install python3-venv; on Windows, repair Python with pip enabled.")
            return 1
    requirement_file = ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(requirement_file.read_bytes()).hexdigest()
    marker = env_dir / ".espresso-requirements.sha256"
    if not marker.exists() or marker.read_text().strip() != fingerprint:
        print("Installing Espresso's libraries (first run or requirements changed)...", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirement_file)], check=True)
        marker.write_text(fingerprint, encoding="utf-8")
    config = ROOT / ".env"
    if not config.exists():
        with config.open("x", encoding="utf-8") as file:
            file.write((ROOT / ".env.example").read_text(encoding="utf-8"))
        if os.name != "nt":
            config.chmod(0o600)
        print("Created .env. Add Discord credentials when ready; website preview works without them.", flush=True)
    args = sys.argv[1:]
    if args == ["--setup"]:
        print("Setup complete. Edit .env, then run the launcher again.")
        return 0
    command = [str(python), "-m", "unittest", "discover", "-s", "tests", "-v"] if args == ["--test"] else [str(python), "-m", "espresso", *args]
    return subprocess.call(command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Setup could not complete ({type(exc).__name__}). Check Python, internet access, and folder permissions.")
        raise SystemExit(1)
