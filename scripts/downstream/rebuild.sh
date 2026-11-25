#!/usr/bin/env bash
# Run only in the disposable Actions checkout; product patches never originate on main.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo 'Use a disposable Actions checkout.' >&2; exit 1; }
: "${GITHUB_OUTPUT:?}" "${GITHUB_RUN_NUMBER:?}"
git diff --quiet
git diff --cached --quiet
[[ -z "$(git ls-files --others --exclude-standard)" ]] || { echo 'Worktree is not clean.' >&2; exit 1; }

# Ordered source-of-truth branches. Remove a merged/empty patch here only after review.
patches=(feat/openai-streaming)
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream https://github.com/vectorize-io/hindsight.git
git fetch --no-tags upstream +refs/heads/main:refs/remotes/upstream/main
refs=(+refs/heads/main:refs/remotes/origin/main +refs/heads/ci:refs/remotes/origin/ci)
for branch in "${patches[@]}"; do refs+=("+refs/heads/$branch:refs/remotes/origin/$branch"); done
git fetch --no-tags origin "${refs[@]}"
upstream=$(git rev-parse upstream/main)
ci=$(git rev-parse origin/ci)
old_main=$(git rev-parse origin/main)
[[ $(git rev-parse HEAD) == "$ci" ]] || { echo 'Workflow must check out the current ci branch.' >&2; exit 1; }
patch_inputs='[]'
for branch in "${patches[@]}"; do
    sha=$(git rev-parse "origin/$branch")
    patch_inputs=$(jq --arg branch "$branch" --arg sha "$sha" '. + [{branch: $branch, sha: $sha}]' <<< "$patch_inputs")
done
inputs=$(jq -n --arg upstream "$upstream" --arg ci "$ci" --argjson patches "$patch_inputs" \
    '{upstream: $upstream, ci: $ci, patches: $patches}')
previous=$(mktemp)
trap 'rm -f "$previous"' EXIT
if git show "$old_main:.downstream/provenance.json" > "$previous" 2>/dev/null; then
    tag=$(jq -er '.release_tag | select(startswith("downstream/v"))' "$previous")
    git fetch origin "refs/tags/$tag:refs/tags/$tag"
    [[ $(git rev-parse "$tag^{commit}") == "$old_main" ]] || {
        echo 'main contains an unknown update after its recorded release; refusing to overwrite it.' >&2; exit 1;
    }
    if [[ $(jq -cS '.inputs' "$previous") == "$(jq -cS . <<< "$inputs")" && ${FORCE_REBUILD:-false} != true ]]; then
        echo 'changed=false' >> "$GITHUB_OUTPUT"
        echo 'Upstream, patches and ci are unchanged.'
        exit 0
    fi
else
    # Bootstrap only over a genuine upstream ancestor, never over unrecorded fork work.
    git merge-base --is-ancestor "$old_main" "$upstream" || {
        echo 'Unmanaged main is not an upstream ancestor; review it before bootstrapping.' >&2; exit 1;
    }
fi

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git switch --detach "$upstream"
for branch in "${patches[@]}"; do
    sha=$(git rev-parse "origin/$branch")
    git merge --squash "$sha"
    if git diff --cached --quiet; then
        echo "Patch $branch is empty: investigate upstream merge/removal before retiring it." >&2
        exit 1
    fi
    git commit -m "chore(downstream): integrate $branch" -m "Patch-Source: $sha"
done

# README is explicitly downstream-owned. All other conflicts fail closed.
if ! git merge --squash "$ci"; then
    mapfile -t conflicts < <(git diff --name-only --diff-filter=U)
    [[ ${#conflicts[@]} == 1 && ${conflicts[0]} == README.md ]] || {
        echo 'Unexpected ci overlay conflict; fix it on ci, not on the candidate.' >&2; exit 1;
    }
fi
git restore --source="$ci" --staged --worktree -- README.md
[[ -z "$(git diff --name-only --diff-filter=U)" ]]
base_version=$("${PYTHON:-python3}" -c 'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("hindsight-api-slim/pyproject.toml").read_text())["project"]["version"])')
version="${base_version}-fork.${GITHUB_RUN_NUMBER}-slim"
release_tag="downstream/v${base_version}-r${GITHUB_RUN_NUMBER}"
mkdir -p .downstream
jq -n --arg version "$version" --arg release_tag "$release_tag" --argjson inputs "$inputs" \
    '{version: $version, release_tag: $release_tag, inputs: $inputs}' > .downstream/provenance.json
git add .downstream/provenance.json
git commit -m 'chore(downstream): integrate ci overlay' -m "CI-Source: $ci" -m "Upstream-Source: $upstream"
{
    echo 'changed=true'
    echo "old_main=$old_main"
    echo "candidate=$(git rev-parse HEAD)"
    echo "version=$version"
    echo "release_tag=$release_tag"
    echo "image=ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/hindsight"
} >> "$GITHUB_OUTPUT"
