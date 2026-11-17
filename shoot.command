#!/bin/bash
set -eu
cd "$(dirname "$0")"
export DIVOOM_STUDIO_CACHE="${DIVOOM_STUDIO_CACHE:-$PWD/.cache}"
exec python3 shoot.py "$@"
