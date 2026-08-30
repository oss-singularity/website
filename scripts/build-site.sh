#!/bin/sh
set -eu

repo_root=$(
  CDPATH=''
  cd -- "$(dirname -- "$0")/.."
  pwd
)
output=${1:-"$repo_root/dist"}
manifest_tmp=$(mktemp)
trap 'rm -f -- "$manifest_tmp"' 0 HUP INT TERM

case "$output" in
  /|"$repo_root"|"$repo_root/site")
    printf 'refusing unsafe build output: %s\n' "$output" >&2
    exit 1
    ;;
esac

rm -rf -- "$output"
mkdir -p \
  "$output/assets/brand" \
  "$output/assets/projects" \
  "$output/assets/scripts" \
  "$output/assets/social" \
  "$output/assets/styles"

copy_file() {
  source_path=$1
  target_path=$2
  if [ ! -f "$repo_root/site/$source_path" ]; then
    printf 'missing site source: %s\n' "$source_path" >&2
    exit 1
  fi
  cp -- "$repo_root/site/$source_path" "$output/$target_path"
}

copy_file index.html index.html
copy_file 404.html 404.html
copy_file .htaccess .htaccess
copy_file robots.txt robots.txt
copy_file sitemap.xml sitemap.xml
copy_file site.webmanifest site.webmanifest
copy_file assets/brand/oss-singularity-mark.svg assets/brand/oss-singularity-mark.svg
copy_file assets/projects/pdrive-control-center-v080.webp assets/projects/pdrive-control-center-v080.webp
copy_file assets/projects/chatgpt-usage-v030.webp assets/projects/chatgpt-usage-v030.webp
copy_file assets/projects/nemo-action-bar.webp assets/projects/nemo-action-bar.webp
copy_file assets/scripts/reactive-field.js assets/scripts/reactive-field.js
copy_file assets/social/oss-singularity-social-preview.png assets/social/oss-singularity-social-preview.png
copy_file assets/styles/site-v0.css assets/styles/site-v0.css

(
  cd "$output"
  find . -type f ! -name dist-manifest.sha256 -print0 \
    | sort -z \
    | xargs -0 sha256sum > "$manifest_tmp"
)
mv -- "$manifest_tmp" "$output/dist-manifest.sha256"

printf 'built %s\n' "$output"
