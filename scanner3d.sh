#!/usr/bin/env bash
# Lance le scanner 3D avec l'environnement Python du projet.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
exec ./.venv/bin/python -m scanner3d "$@"
