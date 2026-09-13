"""
repl.py — subprocess-backed, stateful Python REPL.

Wraps kernel.py as a real child process, talked to only over stdin/stdout
pipes. Namespace state persists across calls to `run()` for the lifetime of
the subprocess, like a notebook kernel.

The child gets an explicit, minimal environment rather than inheriting the
parent's. Two reasons:
  * hygiene — model-generated code shouldn't see anything it doesn't need;
  * correctness inside a Docker Sandbox — the proxy variables
    (HTTP_PROXY / HTTPS_PROXY / NO_PROXY) and the CA-bundle variables MUST be
    forwarded, or the kernel's own Anthropic calls can't reach the host proxy
    that injects the real credential. `kernel_env()` below is the single
    place that list lives.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Iterable, Optional

SENTINEL = "___END_OF_EXEC___"
_KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel.py")

# Variables the kernel needs to function and to reach the sandbox proxy.
_PASSTHROUGH_VARS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    # Docker Sandbox egress proxy — required for llm_call() inside the kernel.
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    # CA bundle so Python's httpx trusts the proxy's TLS interception.
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    # The credential env var. Inside the sandbox this is the literal string
    # "proxy-managed" (see kit/spec.yaml -> apiKey.proxyManaged), which is
    # exactly why it's safe to hand to the kernel there.
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
)


def kernel_env(extra: Optional[dict] = None, passthrough: Iterable[str] = _PASSTHROUGH_VARS) -> dict:
    env = {k: os.environ[k] for k in passthrough if k in os.environ}
    env.setdefault("PYTHONUNBUFFERED", "1")
    if extra:
        env.update(extra)
    return env


class SubprocessREPL:
    def __init__(self, env: Optional[dict] = None, python_executable: str = sys.executable):
        self._proc = subprocess.Popen(
            [python_executable, "-u", _KERNEL_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,  # kernel-crash diagnostics only, not cell output
            text=True,
            bufsize=1,
            env=env if env is not None else kernel_env(),
        )

    def run(self, code: str) -> str:
        """Send a code block to the REPL and return everything it printed."""
        if self._proc.poll() is not None:
            raise RuntimeError("REPL subprocess has exited")

        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(code)
        if not code.endswith("\n"):
            self._proc.stdin.write("\n")
        self._proc.stdin.write(SENTINEL + "\n")
        self._proc.stdin.flush()

        out_lines = []
        while True:
            line = self._proc.stdout.readline()
            if line == "":
                raise RuntimeError("REPL subprocess closed stdout unexpectedly")
            if line.rstrip("\n") == SENTINEL:
                break
            out_lines.append(line)
        return "".join(out_lines)

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def __enter__(self) -> "SubprocessREPL":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
