"""Atomic, recoverable installer support. Only install.sh opts into this code.

Tests call this against disposable fixture directories, never ~/.claude.
Legacy statusline-command.sh and all cache logs/databases are left untouched.
"""
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

BUNDLE_FILES = ("statusline-command.sh", "lib/cache_history.py", "lib/cache_sources.py",
                "lib/statusline_input.py")


def settings_command(bundle):
    return "bash " + shlex.quote(str(Path(bundle).resolve() / "statusline-command.sh"))


def read_settings(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("settings.json symlink: manage it explicitly before installing")
    if not path.exists():
        return {}
    settings = json.loads(path.read_text())
    if not isinstance(settings, dict):
        raise ValueError("settings.json must be a JSON object")
    return settings


def prepare_settings(settings, bundle):
    result = dict(settings)
    status = dict(settings["statusLine"]) if isinstance(settings.get("statusLine"), dict) else {}
    status.update(type="command", command=settings_command(bundle))
    result["statusLine"] = status
    return result


def validate_runtime(source, executable):
    """Probe the very executable that will be pinned, before any config writes."""
    runtime = Path(executable).expanduser().absolute()
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError("Python runtime is not executable")
    code = ("import sys, sqlite3; "
            "sys.exit('Python 3.9+ required') if sys.version_info < (3, 9) else None; "
            "sys.path.insert(0, sys.argv[1]); "
            "import statusline_input, cache_sources, cache_history; "
            "c=sqlite3.connect(':memory:'); c.execute('SELECT 1'); c.close()")
    try:
        probe = subprocess.run([str(runtime), "-c", code, str(source / "lib")],
                               capture_output=True, text=True, timeout=10,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Python runtime probe failed") from exc
    if probe.returncode:
        raise ValueError("Python runtime cannot load the bundle or SQLite")
    return str(runtime)


def install(source, config, failpoint=None, python_executable=None):
    """Stage both targets; back up and roll back any partially activated bundle."""
    source, config = Path(source).resolve(), Path(config).expanduser().absolute()
    for relative in BUNDLE_FILES:
        if not (source / relative).is_file():
            raise ValueError("missing bundle dependency: " + relative)
    runtime = validate_runtime(source, python_executable or sys.executable)
    settings_path, bundle = config / "settings.json", config / "statusline-cockpit"
    read_settings(settings_path)  # Validate BEFORE creating config/backups/staging.
    if bundle.is_symlink():
        raise ValueError("bundle path must not be a symlink")
    config.mkdir(parents=True, exist_ok=True)
    with (config / ".statusline-cockpit-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = read_settings(settings_path)
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + str(time.time_ns())
        backup_bundle = config / ("statusline-cockpit.bak-" + stamp)
        backup_settings = config / ("settings.json.bak-" + stamp)
        stage = Path(tempfile.mkdtemp(prefix=".cockpit-stage-", dir=config))
        had_settings = settings_path.exists()
        moved_old = activated = configured = False
        try:
            for relative in BUNDLE_FILES:
                dest = stage / "bundle" / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, dest)
            (stage / "bundle/statusline-command.sh").chmod(0o755)
            # Data, never shell code: read with IFS=read and quote at execution.
            (stage / "bundle/.python-path").write_text(runtime + "\n")
            new_settings = stage / "settings.json"
            with new_settings.open("w") as output:
                json.dump(prepare_settings(settings, bundle), output, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            new_settings.chmod(settings_path.stat().st_mode & 0o777 if had_settings else 0o600)
            if had_settings:
                shutil.copy2(settings_path, backup_settings)
            if failpoint:
                failpoint("staged")
            if bundle.exists():
                os.replace(bundle, backup_bundle)
                moved_old = True
            os.replace(stage / "bundle", bundle)
            activated = True
            if failpoint:
                failpoint("activated")
            os.replace(new_settings, settings_path)
            configured = True
            if failpoint:
                failpoint("configured")
            return {"bundle": str(bundle), "command": settings_command(bundle),
                    "python_executable": runtime,
                    "backup_bundle": str(backup_bundle) if moved_old else None,
                    "backup_settings": str(backup_settings) if had_settings else None}
        except BaseException:
            if configured:
                if had_settings:
                    shutil.copy2(backup_settings, stage / "restored-settings.json")
                    os.replace(stage / "restored-settings.json", settings_path)
                else:
                    os.replace(settings_path, config / ("settings.json.failed-" + stamp))
            if activated:
                os.replace(bundle, config / ("statusline-cockpit.failed-" + stamp))
            if moved_old:
                os.replace(backup_bundle, bundle)
            raise
        finally:
            # Only this generated temporary stage; never existing caches/user files.
            shutil.rmtree(stage)


def main():
    if sys.version_info < (3, 9):
        raise SystemExit("install: Python 3.9+ required")
    try:
        result = install(sys.argv[1], sys.argv[2])
    except (OSError, ValueError) as exc:
        raise SystemExit("install: " + str(exc))
    print(json.dumps(result, indent=2))
    print("Installed bundle. Existing script/logs retained. Open a new Claude Code session.")


if __name__ == "__main__":
    main()
