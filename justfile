# Bump <project>'s version (from its conventional commits unless <bump> is
# major, minor or patch) and add the release's section to <project>/CHANGELOG.md.
release project bump="auto":
    #!/usr/bin/env bash
    set -euo pipefail
    # Authenticates git-cliff's GitHub username lookups.
    export GITHUB_TOKEN="${GITHUB_TOKEN:-$(gh auth token)}"
    export GIT_CLIFF__BUMP__INITIAL_TAG="{{ project }}/v0.1.0"
    cliff=(git-cliff --tag-pattern "{{ project }}/v.*" --include-path "{{ project }}/**/*" --bump "{{ bump }}")
    tag=$("${cliff[@]}" --bumped-version)
    if [[ -f {{ project }}/CHANGELOG.md ]]; then
        "${cliff[@]}" --unreleased --prepend {{ project }}/CHANGELOG.md
    else
        "${cliff[@]}" --output {{ project }}/CHANGELOG.md
    fi
    # Only re-lock projects that have their own lockfile.
    [[ -f {{ project }}/uv.lock ]] || frozen=--frozen
    uv version --directory {{ project }} --no-sync ${frozen:-} "${tag#*/v}"
    echo "Commit, then tag the release with: git tag $tag"
