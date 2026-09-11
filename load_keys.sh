#!/usr/bin/env bash
# load_keys.sh — read API keys from keys.txt and export them for manuscript-agent.
#
# This script holds NO secrets (safe to commit). Keys live in keys.txt (gitignored).
# keys.txt format — one "Provider:key" per line:
#     Anthropic:sk-ant-...
#     OpenAI:sk-proj-...
#
# Usage:  source load_keys.sh                                 # reads ./keys.txt
#         source load_keys.sh "/path with spaces/keys.txt"    # explicit path (quote it)
#         KEYS_FILE="/path/keys.txt" source load_keys.sh      # or set once via env
# Must be `source`d (not executed) so the exports persist in your shell.
#
# manuscript-agent also reads keys.txt itself at startup (same format, same lookup as .env),
# so sourcing this is only needed when you want the keys in your shell for other tools.

KEYS_FILE="${1:-${KEYS_FILE:-keys.txt}}"
if [[ ! -f "$KEYS_FILE" ]]; then
    echo "load_keys: '$KEYS_FILE' not found — create it with lines like 'Anthropic:sk-ant-...'" >&2
    return 1 2>/dev/null || exit 1
fi

while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"                                  # strip trailing CR (Windows files)
    [[ -z "${line// /}" || "${line#\#}" != "$line" ]] && continue   # skip blank / comment
    provider="${line%%:*}"                                # text before the first colon
    key="${line#*:}"                                      # everything after the first colon
    provider="$(echo "$provider" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    key="$(echo "$key" | xargs)"                          # trim surrounding whitespace
    [[ -z "$key" ]] && continue
    case "$provider" in
        anthropic|claude) export ANTHROPIC_API_KEY="$key" ;;
        openai|gpt)       export OPENAI_API_KEY="$key" ;;
        *) echo "load_keys: unknown provider '$provider' — skipped (manuscript-agent uses Anthropic and OpenAI only)" >&2 ;;
    esac
done < "$KEYS_FILE"

# Report EVERY provider this script can export, including the absent ones, so "missing" is
# distinguishable from "no mapping". NEVER interpolate a key variable directly: `${VAR:-x}`
# expands to the VALUE when VAR is set — it is a default-if-empty operator, not a mask.
_lk_status() { [ -n "${1:-}" ] && printf 'set' || printf '-'; }
echo "loaded from $KEYS_FILE -> ANTHROPIC=$(_lk_status "${ANTHROPIC_API_KEY:-}") OPENAI=$(_lk_status "${OPENAI_API_KEY:-}")"
unset -f _lk_status
