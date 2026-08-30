#!/bin/sh
set -eu

git diff --check
git diff --cached --check

for required in README.md SECURITY.md docs/hosting.md docs/brand-inputs.md; do
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

printf 'repository baseline checks passed\n'
