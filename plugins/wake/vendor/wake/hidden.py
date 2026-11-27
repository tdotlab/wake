"""Run `python -m <module> [args]` with no visible window and append its output to a log.

For Windows Task Scheduler:  pythonw.exe -m wake.hidden <log-name> <module> [args...]
pythonw has no console; the child runs in a hidden one that its own children (git, claude) share,
so nothing flashes on screen. Logs: ~/.wake/logs/<log-name>.log, trimmed to the last 1 MB.
"""
from datetime import datetime
from pathlib import Path
import subprocess
import sys

LOG_MAX = 2_000_000
LOG_KEEP = 1_000_000


def trim(log):
    if log.is_file() and log.stat().st_size > LOG_MAX:
        data = log.read_bytes()[-LOG_KEEP:]
        log.write_bytes(data[data.find(b'\n') + 1:])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print(__doc__)
        return 2
    name, cmd = argv[0], argv[1:]
    logs = Path.home() / '.wake' / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f'{name}.log'
    trim(log)
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with log.open('a', encoding='utf-8') as f:
        f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd)}\n")
        f.flush()
        # -u: unbuffered, so the log fills as the run goes and survives a crash or a killed task.
        run = subprocess.run([str(python), '-u', '-X', 'utf8', '-m', *cmd], stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=flags)
        f.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} exit {run.returncode}\n")
    return run.returncode


if __name__ == '__main__':
    sys.exit(main())
