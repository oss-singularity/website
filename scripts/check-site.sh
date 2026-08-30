#!/bin/sh
set -eu

repo_root=$(
  CDPATH=''
  cd -- "$(dirname -- "$0")/.."
  pwd
)

"$repo_root/scripts/build-site.sh"
python3 "$repo_root/scripts/check-site.py" "$repo_root/dist"

printf 'static site checks passed\n'
