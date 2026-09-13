"""
rlm.py — Recursive Language Model (RLM) orchestrator.

Implements the inference loop from Zhang, Kraska & Khattab, "Recursive
Language Models" (arXiv:2512.24601), Algorithm 1:

    state <- InitREPL(prompt=P)               # context lives in a REPL variable
    state <- AddFunction(state, sub_RLM)      # llm_call() available in the REPL
    hist  <- [Metadata(state)]               # root LM sees only length, not content
    loop:
        code            <- LLM(hist)         # root LM writes code
        (state, stdout) <- REPL(state, code) # executed in the persistent kernel
        hist            <- hist || code || Metadata(stdout)   # truncated stdout
        if state[Final] is set: return state[Final]

Design choices, and how they map to the sandbox:

  * The REPL is a real child process (kernel.py via repl.py).
  * Recursion depth = 1 (paper default): `llm_call()` inside the kernel is a
    plain sub-LM call, not a nested RLM.
  * Both this process (root model) and the kernel (sub-calls) talk to
    api.anthropic.com DIRECTLY. There is no localhost bridge. Inside a Docker
    Sandbox, `ANTHROPIC_API_KEY` is the `proxy-managed` sentinel and the
    host-side proxy injects the real x-api-key header on the way out. The
    real key never enters the VM.
  * Termination is the paper's: the model sets a REPL variable named `Final`.
    After every cell we probe the kernel for it. No FINAL()/FINAL_VAR() text
    tags to regex for.
"""

from __future__ import annotations

import json
import re
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from .repl import SubprocessREPL, kernel_env

DEFAULT_ROOT_MODEL = "claude-sonnet-5"
DEFAULT_SUB_MODEL = "claude-haiku-4-5-20251001"

_CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)

SYSTEM_PROMPT = textwrap.dedent("""\
    You are the root model in a Recursive Language Model (RLM) system.

    You do NOT see the full input directly. It is stored in a Python variable
    called `context` inside a persistent Python REPL. You interact with it
    ONLY by writing Python code in fenced ```python``` blocks. Whatever the
    code prints is sent back to you, truncated if long. Variables persist
    between your turns.

    Available in the REPL:
      - context: str — the full input you must answer a question about.
      - llm_call(prompt: str, model: str | None = None) -> str — calls a
        smaller recursive language model on a text prompt and returns its
        answer. Use it inside loops to map, extract, classify, or summarize
        over chunks of `context`, instead of reading large spans yourself.

    Strategy: peek at the first few thousand characters to learn structure;
    grep with regex / str methods to narrow down; split into chunks and call
    llm_call() on each for semantic sub-questions; accumulate results in
    variables. Keep printed output small — print counts, samples, and
    summaries, not whole slices.

    To finish, assign your answer to a variable named `Final` in a code block:

        Final = "<your complete answer as a string>"

    The loop ends as soon as `Final` exists. Do not set it until you are
    confident. Build long answers up in variables and then assign them to
    `Final` — `Final` can be as long as it needs to be.
""")

# Python source executed inside the kernel to define `llm_call`. Overridable
# (see RLM.subcall_bootstrap) so tests and offline runs can stub it out.
DEFAULT_SUBCALL_BOOTSTRAP = textwrap.dedent("""\
    import anthropic as _anthropic
    _sub_client = _anthropic.Anthropic()

    def llm_call(prompt, model=None):
        resp = _sub_client.messages.create(
            model=model or SUB_MODEL,
            max_tokens=SUB_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")
""")

_FINAL_PROBE = (
    "import json as _json\n"
    "print(_json.dumps({'set': 'Final' in globals(), 'value': globals().get('Final')}, default=str))\n"
)


def extract_code(reply: str) -> Optional[str]:
    """All fenced python blocks in the reply, concatenated in order."""
    blocks = _CODE_RE.findall(reply)
    return "\n".join(b for b in blocks) if blocks else None


def truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n... [truncated, {len(s) - n} more characters]"


@dataclass
class RLM:
    client: anthropic.Anthropic
    root_model: str = DEFAULT_ROOT_MODEL
    sub_model: str = DEFAULT_SUB_MODEL
    max_turns: int = 20
    max_output_chars: int = 4000
    root_max_tokens: int = 4096
    sub_max_tokens: int = 2048
    verbose: bool = True
    subcall_bootstrap: str = DEFAULT_SUBCALL_BOOTSTRAP
    kernel_extra_env: dict = field(default_factory=dict)

    # ---- logging -----------------------------------------------------------

    def _log(self, *args) -> None:
        if self.verbose:
            print(*args, flush=True)

    # ---- model calls --------------------------------------------------------

    def _call_root(self, messages: list[dict]) -> str:
        resp = self.client.messages.create(
            model=self.root_model,
            max_tokens=self.root_max_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    # ---- kernel setup -------------------------------------------------------

    def _bootstrap(self, repl: SubprocessREPL, context: str) -> None:
        # The context goes through a temp file rather than a string literal so
        # arbitrary content (quotes, backslashes, NULs) can't break the cell.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(context)
            context_path = f.name

        cell = (
            f"SUB_MODEL = {self.sub_model!r}\n"
            f"SUB_MAX_TOKENS = {self.sub_max_tokens}\n"
            f"with open({context_path!r}, encoding='utf-8') as _f:\n"
            f"    context = _f.read()\n"
            f"{self.subcall_bootstrap}\n"
            f"print(f'Context loaded: {{len(context)}} characters')\n"
        )
        out = repl.run(cell)
        self._log(out.strip())
        if "Traceback" in out:
            raise RuntimeError(f"kernel bootstrap failed:\n{out}")

    def _check_final(self, repl: SubprocessREPL) -> Optional[str]:
        out = repl.run(_FINAL_PROBE).strip().splitlines()
        if not out:
            return None
        try:
            probe = json.loads(out[-1])
        except json.JSONDecodeError:
            return None
        if not probe.get("set"):
            return None
        value = probe.get("value")
        return value if isinstance(value, str) else str(value)

    # ---- main loop ----------------------------------------------------------

    def complete(self, query: str, context: str) -> str:
        """Run the RLM loop and return the final answer string."""
        with SubprocessREPL(env=kernel_env(extra=self.kernel_extra_env)) as repl:
            self._bootstrap(repl, context)

            messages: list[dict] = [{
                "role": "user",
                "content": (
                    f"Query: {query}\n\n"
                    f"`context` is loaded in your REPL ({len(context):,} characters). "
                    f"Start by inspecting its structure."
                ),
            }]

            for turn in range(1, self.max_turns + 1):
                self._log(f"\n--- root LM turn {turn} ---")
                reply = self._call_root(messages)
                self._log(reply.strip())
                messages.append({"role": "assistant", "content": reply})

                code = extract_code(reply)
                if code is None:
                    messages.append({
                        "role": "user",
                        "content": (
                            "No ```python``` block found. Write code to keep working "
                            "with `context`, or assign your answer to `Final`."
                        ),
                    })
                    continue

                output = repl.run(code)

                final = self._check_final(repl)
                if final is not None:
                    self._log("\n[Final set — done]")
                    return final

                messages.append({
                    "role": "user",
                    "content": "Execution output:\n" + truncate(output, self.max_output_chars),
                })

        raise RuntimeError(f"no `Final` after {self.max_turns} turns")
