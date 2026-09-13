# RLM harness — build, test, and run inside a Docker Sandbox.
#
# One-time setup on the host (macOS):
#   brew install docker/tap/sbx          # standalone Docker Sandboxes CLI
#   make secret                          # stores the Anthropic key in Keychain
#
# Typical loop:
#   make test                            # offline unit tests (no API calls)
#   make build push                      # build image, push to your registry
#   make run CONTEXT=./big.txt QUERY="How many errors are in this log?"
#
# NOTE: Docker Sandboxes and kits are experimental and the CLI surface is
# still moving. If `make run` fails on a flag, check `sbx run --help` and
# adjust SBX_RUN below — the kit spec itself is on the current v2 grammar.

SHELL := /bin/bash

# REGISTRY is injectable and has no default. Set it one of three ways:
#   * per invocation:  make build push REGISTRY=docker.io/<your-namespace>
#   * environment:     export REGISTRY=docker.io/<your-namespace>
#   * .env file:       echo 'REGISTRY=docker.io/<your-namespace>' > .env   (gitignored)
-include .env
REGISTRY     ?=
IMAGE_NAME   ?= rlm-harness
VERSION      ?= 0.1.0
IMAGE        := $(REGISTRY)/$(IMAGE_NAME):$(VERSION)

SANDBOX_NAME ?= rlm
KIT_DIR      ?= ./kit
WORKSPACE    ?= $(CURDIR)

CONTEXT      ?=
QUERY        ?=
RLM_ARGS     ?=

PY           ?= python3

.PHONY: help venv lint test build push kit-validate diagnose secret run shell rm status local-run clean need-registry

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ---- local dev ---------------------------------------------------------------

venv: ## create .venv and install the package in dev mode
	$(PY) -m venv .venv && .venv/bin/pip install -q -e '.[dev]'

lint: ## ruff check — pyflakes/pycodestyle defaults + the 79-char limit
	.venv/bin/ruff check rlm/ tests/

test: lint ## run lint, then offline tests (real kernel, faked models)
	.venv/bin/python -m pytest -q tests/

# ---- image -------------------------------------------------------------------

need-registry:
	@test -n "$(REGISTRY)" || (echo "REGISTRY is not set — where should the image live?"; \
	  echo "  make <target> REGISTRY=docker.io/<your-namespace>"; \
	  echo "  or: echo 'REGISTRY=docker.io/<your-namespace>' > .env"; exit 2)

build: need-registry ## build the sandbox image
	docker build -t $(IMAGE) .

push: need-registry ## push the image so the sandbox's private Docker engine can pull it
	docker push $(IMAGE)

# ---- sandbox -----------------------------------------------------------------

kit-validate: ## validate kit/spec.yaml against the sbx schema
	sbx kit validate $(KIT_DIR)

diagnose: ## health-check the sbx installation (daemon, auth, virtualization)
	sbx diagnose

secret: ## store the Anthropic API key in the host keychain (prompts; never touches the VM)
	sbx secret set anthropic
	sbx secret ls

# `sbx run [flags] KIT [PATH...] [-- AGENT_ARGS...]` — the kit is positional
# (--kit is for mixins on built-in agents). `--` separates sbx args from
# arguments passed to the kit entrypoint (rlm).
SBX_RUN = sbx run --kit-arg image=$(IMAGE) --name $(SANDBOX_NAME) $(KIT_DIR) $(WORKSPACE)

run: need-registry ## run an RLM query inside the sandbox: make run CONTEXT=file QUERY='...'
	@test -n "$(CONTEXT)" -a -n "$(QUERY)" || (echo "usage: make run CONTEXT=file QUERY='...'" && exit 2)
	$(SBX_RUN) -- --context "$(abspath $(CONTEXT))" --query "$(QUERY)" $(RLM_ARGS)

shell: need-registry ## open the sandbox in interactive mode (entrypoint --help)
	$(SBX_RUN)

status: ## list sandboxes (there is no `sbx logs`; use `sbx tui` for a live dashboard)
	sbx ls

rm: ## delete the sandbox VM and everything in it
	sbx rm $(SANDBOX_NAME)

clean: rm ## remove sandbox + local build artifacts
	rm -rf .venv build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +

# ---- agent fleet -------------------------------------------------------------

.PHONY: run-fleet edit-fleet kill-fleet restart-fleet

# Boot this directory's agent fleet (joins the shared tmux agent dashboard)
run-fleet:
	agent-boot --settings harness/settings.json

# Settings editor with validation + autosave (PORT=… to change the port)
edit-fleet:
	bun harness/serve-settings.ts

kill-fleet:
	agent-boot --settings harness/settings.json --kill

# Relaunch the fleet with the current config (stops sessions and containers first)
restart-fleet:
	agent-boot --settings harness/settings.json --restart
