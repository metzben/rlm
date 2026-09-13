# RLM harness in a Docker Sandbox

A Recursive Language Model (Zhang, Kraska & Khattab, [arXiv:2512.24601](https://arxiv.org/abs/2512.24601))
that runs entirely inside a Docker Sandbox microVM, with the Anthropic API key
injected by the host-side proxy so it never enters the VM.

```
 host (macOS)                              │  sandbox microVM
 ─────────────────────────────────────────  │  ──────────────────────────────────────
 Keychain ── real ANTHROPIC_API_KEY         │  rlm (root-model loop)  ──┐
     │                                      │     └─ kernel.py (REPL)   ├─► HTTPS ─┐
 sbx proxy ◄────────────────────────────────┼──── ANTHROPIC_API_KEY=proxy-managed ◄┘
     │  rewrites x-api-key on the way out   │
     └──► api.anthropic.com:443 (only allowed egress)
```

## Layout

```
rlm/kernel.py      stateful Python REPL subprocess (the RLM "environment")
rlm/repl.py        SubprocessREPL + the explicit env the kernel is allowed to see
rlm/rlm.py         orchestrator: Algorithm 1 from the paper, Final-variable termination
rlm/cli.py         `rlm --context FILE --query "..."` (also the sandbox entrypoint)
kit/spec.yaml      Docker Sandboxes kit: network allow-list + proxy-managed credential
Dockerfile         image on docker/sandbox-templates:shell-docker (no ENTRYPOINT — see field notes)
Makefile           build / test / sandbox lifecycle
tests/             offline tests (real kernel, faked models)

rlm-harness-docs.html      full system documentation with diagrams (start here)
rlm-paper-2512.24601.pdf   the RLM paper (Zhang, Kraska & Khattab)
```

## Setup (once)

```bash
brew install docker/tap/sbx        # standalone CLI (Docker Desktop's old `docker sandbox` is removed)
sbx login                          # sandboxes require a Docker sign-in
sbx policy init deny-all           # one-time global egress baseline; the kit's allow layers on top
make venv test                     # offline tests
make secret                        # `sbx secret set anthropic` → stored host-side (prompts for the key)
make build push                    # REGISTRY defaults to docker.io/austerelabs; override REGISTRY=... to change
```

The sandbox runs its own private Docker engine and pulls the image itself.
Host-local images are not visible to it (the pull fails with a bare `403`) —
hence `push`.

## Run

```bash
make run CONTEXT=./logs/big.log QUERY="How many distinct error codes appear, and which is most common?"
```

First run: `sbx` prompts you to approve the `anthropic` credential binding for
this kit and its declared domain. Approve once; it's remembered (in
`~/.config/sbx/credentials.yaml`). A **non-interactive** first run does not
prompt — it warns and starts the sandbox *without* the credential — so do the
first run in a terminal you're watching. After that, unattended runs
(`--detached`, CI) just work.

## Security model — what you get and what you don't

You **do** get, from Docker Sandboxes:

- Hypervisor isolation: the harness runs on its own kernel, not the host's.
- Deny-by-default egress; only `api.anthropic.com:443` is reachable.
- The real API key stays in Keychain. Both processes in the VM see
  `ANTHROPIC_API_KEY=proxy-managed`; the proxy injects the real header.

You **don't** get isolation *between* the two processes inside the VM. The
orchestrator and `kernel.py` share a filesystem and a network namespace. That's
acceptable here because the one secret that matters isn't in the VM to steal —
but `repl.py` still hands the kernel a minimal explicit environment rather than
the parent's, and `tests/test_rlm.py` pins that behavior.

`make local-run` runs outside the sandbox with a real key. In that mode the key
*does* reach the kernel (it has to, for `llm_call`). Use it for development on
trusted inputs only.

## Field notes — verified against `sbx` 0.42.1

Docker Sandboxes and kits are experimental; these are the things that actually
bit during bring-up (2026-09-12), recorded so they don't bite twice. The full
story is in [`rlm-harness-docs.html`](./rlm-harness-docs.html).

1. **`sbx run` grammar.** The kit is a *positional* argument:
   `sbx run ./kit WORKSPACE -- <args>`. The `--kit` flag exists but means
   "mixin on a built-in agent" — a different thing. There is also no
   `sbx logs`; use `sbx ls` / `sbx tui`, and when a container fails to start,
   the real error is in the daemon log under
   `~/Library/Application Support/com.docker.sandboxes/`.
2. **Never set an `ENTRYPOINT` in the image.** The runtime needs the
   template's `tini` as the container's main process; it injects a
   `start-agent` helper that then launches the kit's declared entrypoint.
   Overriding it makes the container exit at birth, surfacing only as the
   cryptic `started hook: … container is not running`.
3. **TLS to the proxy works as configured.** The Dockerfile points
   `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` at the system CA store, where the
   sandbox template installs the proxy CA. Verified live for both the root
   loop and `llm_call()` sub-calls.
4. **The image must be pushed.** The sandbox's private Docker engine pulls
   from registries only; a host-local tag fails with a bare `403`.

## How it maps to the paper

| Paper (Algorithm 1)                     | Here                                              |
| --------------------------------------- | ------------------------------------------------- |
| `InitREPL(prompt=P)`                    | `_bootstrap()` loads `context` into `kernel.py`   |
| `AddFunction(state, sub_RLM)`           | `llm_call()` defined in the kernel (depth = 1)    |
| `hist ← [Metadata(state)]`              | first message carries only `len(context)`         |
| `hist ← hist ‖ code ‖ Metadata(stdout)` | `truncate(output, max_output_chars)`              |
| `if state[Final] is set: return`        | `_check_final()` probes the kernel every cell     |

Recursion depth is 1 (the paper's default). Depth > 1 — `llm_call` spawning a
nested `RLM` with its own kernel — is a contained change to `subcall_bootstrap`.
