#!/bin/sh
set -eu

repo_root=$(
    CDPATH=''
    cd -- "$(dirname -- "$0")/.."
    pwd
)
cd "$repo_root"

git diff --check
git diff --cached --check

for required in README.md CONTRIBUTING.md LICENSE BRANDING.md SECURITY.md docs/hosting.md docs/brand-inputs.md docs/commons-requirements.md docs/theme-behavior.md; do
    if [ ! -s "$required" ]; then
        printf 'missing required repository baseline: %s\n' "$required" >&2
        exit 1
    fi
done

tracked_files=$(git ls-files)
if printf '%s\n' "$tracked_files" \
    | grep -E '(^|/)(\.env($|\.)|.*\.(key|pem|p12|pfx)$)' \
    | grep -v -E '(^|/)\.env\.example$'; then
    printf 'refusing tracked credential-shaped file\n' >&2
    exit 1
fi

if git grep -nEI \
    'cpsess[0-9]+|CPANEL_API_TOKEN|Authorization:[[:space:]]*cpanel|BEGIN (OPENSSH|RSA|EC) PRIVATE KEY' \
    -- . ':!scripts/check-repository.sh'; then
    printf 'possible secret or session material found\n' >&2
    exit 1
fi

"$repo_root/scripts/check-site.sh"

ui_report=$(mktemp)
if ! node --test scripts/test-mission-handoff.mjs scripts/test-theme.mjs \
    scripts/test-work-items-ui.mjs scripts/test-projects-ui.mjs \
    scripts/test-road-so-far.mjs scripts/test-commons-activity.mjs \
    scripts/test-commons-growth-data.mjs > "$ui_report"; then
    cat "$ui_report"
    rm -f "$ui_report"
    printf 'UI test suites failed\n' >&2
    exit 1
fi
rm -f "$ui_report"

printf 'repository baseline checks passed\n'
