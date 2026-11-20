# Downstream maintenance

This fork is a maintained patch stack over `vectorize-io/hindsight`, not a hard fork.
The supported downstream artifact is the **API + control-plane slim image** for
`linux/amd64` only, published to `ghcr.io/johnsonran/hindsight`.
It does not bundle local embedding/reranking models; configure remote providers.

## Sources of truth

- `upstream/main`: the fetched upstream baseline, pinned by SHA for each build.
- `feat/openai-streaming`: the independent product patch. Enable SSE with
  `HINDSIGHT_API_LLM_STREAM=true`; defaults remain unchanged.
  Related upstream request: https://github.com/vectorize-io/hindsight/issues/6166.
  The linked DalenW fork adds per-operation flags for consolidation/refresh on chat
  completions only. This patch uses one global transport flag for chat completions and
  Responses. It does not reuse `extra_body.stream`: that dict is forwarded verbatim to every
  provider, so the setting would break Ollama native, Anthropic, Gemini and LiteLLM calls.
- `ci`: this maintenance document, downstream README, workflows and delivery tests.
  Runtime changes never belong here.
- `main`: regenerated integrated releases only, never the source of a fix.

The ordered patch list lives in `scripts/downstream/rebuild.sh`. Integration order:

1. Exact current upstream `main` commit.
2. `feat/openai-streaming`, as one squash integration commit.
3. `ci`, as one squash integration commit, including `.downstream/provenance.json`.

There are currently no compatibility, ecosystem or intentional-divergence overrides.
`README.md` is downstream-owned and is restored verbatim from `ci` during integration;
every other conflict stops the release. Do not add catch-all conflict resolvers.

Fix a product defect on its patch branch; fix automation on `ci`; regenerate `main`.
Never repair only `main`, rebase it onto its previous downstream history, or use it as
another patch's baseline. Keep patch branches independently testable on upstream.

## Automated delivery

`.github/workflows/downstream.yml` runs on `ci` pushes, manual dispatch and daily at
**02:26 Asia/Shanghai (18:26 UTC the preceding day)**. The schedule becomes active
when the first successful release installs the workflow on default branch `main`.
The first run bootstraps directly from a `ci` push; no unvalidated bootstrap commit
is needed on `main`. One shared concurrency group serializes all deliveries.

The workflow:

1. Fetches upstream and exact fork branch inputs. Unchanged inputs skip rebuilding;
   manually dispatch with `force=true` to refresh base-image security updates.
2. Rebuilds from upstream, recording every input SHA. An empty patch stops rather
   than silently retiring it; inspect upstream first, then remove its integration
   entry and rebuild before deleting the patch branch.
3. Runs maintenance contract tests, focused provider regressions, Ruff, the full API
   type check and non-streaming/streaming blackbox retain/recall stories.
4. Builds the upstream `standalone` target with `INCLUDE_LOCAL_MODELS=false` and
   `PRELOAD_ML_MODELS=false` for `linux/amd64`. A unique `build-*` staging tag
   is not a supported consumer release.
5. Pulls the actual image and starts its shipped API and control plane
   against deterministic provider stubs. It checks UI-to-API connectivity and an
   SSE retain/consolidation/recall round trip; no real provider credentials are used.
   Only the client and test harness are mounted, never the product's API source.
   The throwaway test container installs the client using constraints frozen from
   the image's installed packages, so missing test-only dependencies can be added
   without upgrading the product dependencies or changing the published image.
6. Signs and verifies the validated manifest with keyless cosign.
7. Rechecks `main`, `ci` and patch tips. It creates the immutable version, atomically
   pushes candidate `main` plus a `downstream/v…-r…` tag using an explicit main lease,
   then updates `latest-slim` and the SHA alias and publishes GitHub release notes.

No PyPI/npm publication or upstream-style `v*` tags are involved. The `ci` overlay
also guards **every job** in upstream's `release.yml` and `release-integration.yml`
with the upstream repository identity, so even an accidentally pushed upstream-style
tag cannot publish those packages from this fork. A family-wide test checks the guard.
This is release-overlay policy, not a product/runtime override. `GITHUB_TOKEN`
needs `contents: write`, `packages: write` and `id-token: write`; no PAT is needed,
because image build, validation and publication are in the same workflow, not
triggered indirectly by a token-authenticated push.

arm64 is deliberately not built: no arm64 deployment exists. To restore it, re-add
`docker/setup-qemu-action` (or a native ARM runner), add `linux/arm64` to `platforms`
and the verify loop, and update the contract test and this document.
A failure or conflict leaves the previous `main` and consumer image aliases intact
until the promotion step. Git and GHCR are not one atomic transaction: a failed git
lease may leave an orphan immutable version; a failure after git promotion may
leave `latest-slim` on the previous version. Inspect the run, do not force overwrite
unknown changes; repair the owning branch and dispatch again to roll forward.

## Versions, provenance and rollback

- Moving alias: `ghcr.io/johnsonran/hindsight:latest-slim`.
- Immutable fork version: `<upstream-version>-fork.<workflow-run-number>-slim`.
- Source alias: `sha-<candidate-commit-first-12>-slim`.
- Git release/tag: `downstream/v<upstream-version>-r<workflow-run-number>`.
- `.downstream/provenance.json` and its release attachment pin upstream, patch and
  maintenance SHAs; OCI `revision` points to the integrated source commit.

Pin deployments to the published **manifest digest**, not `latest-slim`, for
repeatable upgrades and rollback. To roll back, redeploy a previous release's digest
without rewinding `main` or modifying the patch stack. Back up PostgreSQL before
upgrades: an older application image is not a database-migration rollback strategy.
Upstream may also fetch newer base OS packages for the same code; the digest is the
identity of the actual tested artifact, not a claim of bit-for-bit rebuilds.

GHCR packages can be private even when their source repository is public. If anonymous
pull is denied, the owner must set the package visibility to public in GitHub's
package settings (or deliberately use authenticated pulls). Do not claim public
availability until an anonymous manifest pull succeeds.

Keyless signature verification:

```sh
cosign verify ghcr.io/johnsonran/hindsight@sha256:<release-digest> \
  --certificate-identity-regexp '^https://github\.com/JohnsonRan/hindsight/\.github/workflows/downstream\.yml@refs/heads/(ci|main)$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Actions run logs and each GitHub release record the exact image version, digest,
source commit and provenance. Do not mark delivery complete until the image exists,
the amd64 image has passed the image tests, and its intended pull mode works.
