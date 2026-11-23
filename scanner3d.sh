#!/usr/bin/env bash
# Lance le scanner 3D avec l'environnement Python du projet (arguments : voir `./scanner3d.sh --help`).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec ./.venv/bin/python -m scanner3d "$@"
