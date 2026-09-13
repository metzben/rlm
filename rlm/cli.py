"""
cli.py — command-line entrypoint (also the Docker Sandbox kit entrypoint).

    rlm --context ./big_file.txt --query "How many X are there?"

Inside the sandbox the workspace is mounted at the same absolute path as on
the host, so a path like /Users/you/project/big.txt works unchanged.
"""

from __future__ import annotations

import argparse
import os
import sys

import anthropic

from .rlm import DEFAULT_ROOT_MODEL, DEFAULT_SUB_MODEL, RLM


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rlm", description="Recursive Language Model over a long context.")
    p.add_argument("--context", required=True, help="path to the (potentially huge) text file")
    p.add_argument("--query", required=True, help="the question to answer about the context")
    p.add_argument("--root-model", default=DEFAULT_ROOT_MODEL)
    p.add_argument("--sub-model", default=DEFAULT_SUB_MODEL)
    p.add_argument("--max-turns", type=int, default=20)
    p.add_argument("--quiet", action="store_true", help="print only the final answer")
    args = p.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Inside a Docker Sandbox this is the `proxy-managed` sentinel set by
        # the kit; outside, it must be a real key. Either way it must exist.
        print("error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    with open(args.context, encoding="utf-8", errors="replace") as f:
        context = f.read()

    rlm = RLM(
        client=anthropic.Anthropic(),
        root_model=args.root_model,
        sub_model=args.sub_model,
        max_turns=args.max_turns,
        verbose=not args.quiet,
    )
    try:
        answer = rlm.complete(args.query, context)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not args.quiet:
        print("\n=== FINAL ANSWER ===")
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
