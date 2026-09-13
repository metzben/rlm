"""
kernel.py — a minimal stateful Python REPL driven over stdin/stdout.

This process is the RLM "environment": it holds a persistent namespace and
executes whatever code the orchestrator (rlm.py) sends it, returning the
captured stdout/stderr of each cell.

Protocol: the parent writes code lines followed by a line that is exactly
SENTINEL. This process exec()s the code against its persistent namespace,
prints everything the code emitted, then prints SENTINEL and flushes.

Inside a Docker Sandbox this process runs with ANTHROPIC_API_KEY set to the
`proxy-managed` sentinel, so even `print(os.environ)` from model-generated
code reveals nothing useful — the real key never enters the VM.
"""

import contextlib
import io
import sys
import traceback

SENTINEL = "___END_OF_EXEC___"


def main() -> None:
    ns: dict = {}
    while True:
        lines = []
        hit_sentinel = False
        for line in sys.stdin:
            if line.rstrip("\n") == SENTINEL:
                hit_sentinel = True
                break
            lines.append(line)

        if not hit_sentinel and not lines:
            break  # stdin closed: parent is done with us

        code = "".join(lines)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(code, ns)
        except BaseException:  # noqa: BLE001 — include SystemExit/KeyboardInterrupt from model code
            traceback.print_exc(file=buf)

        print(buf.getvalue())
        print(SENTINEL)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
