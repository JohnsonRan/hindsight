# Hindsight — JohnsonRan's downstream fork

Hindsight is an agent memory system from [vectorize-io/hindsight](https://github.com/vectorize-io/hindsight).
This fork follows upstream and carries a small, independently maintained patch stack.
See **[MAINTAIN.md](./MAINTAIN.md)** before changing patch branches, CI or release history.
For product documentation, see [Hindsight docs](https://hindsight.vectorize.io/).

## Downstream patches

| Branch | Capability | Upstream context |
| --- | --- | --- |
| `feat/openai-streaming` | Opt-in SSE for OpenAI-compatible chat completions and `openai-responses`, behind a dedicated transport setting | [#6166](https://github.com/vectorize-io/hindsight/issues/6166) |

Enable streaming without changing default behavior:

```sh
HINDSIGHT_API_LLM_STREAM=true
```

Hindsight assembles and validates the complete reply internally. Existing output
validation, truncation detection, token counts, deadlines and retries remain in use.
This is provider-request streaming, not a new public streaming API or WebSocket mode.
Releases before this change used `HINDSIGHT_API_LLM_EXTRA_BODY='{"stream":true}'`; remove
that value when upgrading. `extra_body` is forwarded verbatim to every provider.

## Supported container

**API + control plane, slim, linux/amd64 + linux/arm64:**

```text
ghcr.io/johnsonran/hindsight:latest-slim
```

Use a release's fixed version or `@sha256:…` digest for production and rollback.
See [Releases](https://github.com/JohnsonRan/hindsight/releases) for tested versions,
manifest digests, source commits and provenance. Configure remote embeddings and
reranking: the slim image does **not** bundle local ML models.

Example deployment, after preparing your provider configuration in `hindsight.env`:

```sh
docker run -d --name hindsight --restart unless-stopped --stop-timeout 75 \
  -p 127.0.0.1:8888:8888 -p 127.0.0.1:9999:9999 \
  --env-file hindsight.env \
  -v hindsight-data:/home/hindsight/.pg0 \
  ghcr.io/johnsonran/hindsight:latest-slim
```

Ports are loopback-only here; configure authentication and a secure reverse proxy
before exposing the API or control plane. Back up your database before upgrades.
For first-publication package visibility and image-signature verification, see
[MAINTAIN.md](./MAINTAIN.md#versions-provenance-and-rollback).

## Updates

[Downstream release](https://github.com/JohnsonRan/hindsight/actions/workflows/downstream.yml)
checks upstream daily at **02:26 Beijing time**, or on `ci` pushes/manual dispatch.
Only validated source and both tested platform images are promoted. Conflicts and
failed tests stop delivery rather than overwriting the previous usable release.

`ci` owns maintenance; product patches stay on their own branches; `main` is a
regenerated release result. This fork does not publish upstream's PyPI/npm packages.
