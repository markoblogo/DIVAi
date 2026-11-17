#!/bin/bash
set -eu
cd "$(dirname "$0")"
export DIVOOM_STUDIO_CACHE="${DIVOOM_STUDIO_CACHE:-$PWD/.cache}"
mode=--live
if [[ "${1:-}" == "--preview" ]]; then
    mode=--preview
    shift
fi
exec python3 studio.py "$mode" "$@"
