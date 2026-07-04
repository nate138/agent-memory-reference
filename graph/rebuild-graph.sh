#!/usr/bin/env bash
#
# rebuild-graph.sh — reference skeleton for a clean-room, AST-only code-graph
# rebuild trigger.
#
# This replaces an automatic post-commit hook with an EXPLICIT, flag-locked
# rebuild you invoke on demand or at a verification checkpoint. See the project
# README for why an auto post-commit hook is deliberately avoided.
#
# The load-bearing property: deep mode is made STRUCTURALLY UNABLE TO FIRE, not
# merely left unset. On any repository with customer-adjacent data, deep mode
# (which sends code structure to an external LLM) is forbidden. AST-only analysis
# runs entirely locally; nothing leaves the machine.
#
# Usage:
#   rebuild-graph.sh <project-dir> <vault-graph-subdir>
#
# Both arguments are absolute or relative paths YOU provide. This skeleton takes
# the paths as arguments rather than hardcoding any real filesystem location.

set -euo pipefail

PROJECT_DIR="${1:?need project dir}"
VAULT_SUBDIR="${2:?need vault graph subdir}"

# --- Deep-mode refusal (the guardrail that must never be softened) ----------
# Refuse if anyone tries to force deep mode through the environment. This is a
# hard stop, not a warning: the script exits non-zero and rebuilds nothing.
if [[ "${GRAPHIFY_MODE:-}" == "deep" ]]; then
  echo "REFUSED: deep mode is forbidden by policy on this repo." >&2
  exit 1
fi

# --- AST-only rebuild -------------------------------------------------------
# Invoke the parser in its default AST-only mode. No --deep. No --mode deep.
# --update rebuilds only changed files. The parser is passed the project path
# as an argument; this script does not change directories into arbitrary trees.
graphify "$PROJECT_DIR" \
  --update \
  --obsidian \
  --obsidian-dir "$VAULT_SUBDIR"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] graph rebuilt (AST-only): ${PROJECT_DIR} -> ${VAULT_SUBDIR}"
