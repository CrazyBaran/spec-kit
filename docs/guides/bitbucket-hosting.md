# Runbook: Hosting Spec Kit Artifacts on Bitbucket

This runbook walks through configuring a Bitbucket repository as a
distribution source for Spec Kit **extensions**, **bundles**, and
**presets** — including private repositories.

Spec Kit installs everything over plain HTTPS: a catalog is a JSON file
fetched from any HTTPS URL, and each artifact is an archive (`.zip` or
`.tar.gz`) downloaded from the `download_url` in its catalog entry. GitHub
is only the *default* host, not a requirement. Bitbucket works with no
code changes — you just map GitHub concepts onto their Bitbucket
equivalents:

| Purpose | GitHub | Bitbucket Cloud | Bitbucket Data Center |
|---|---|---|---|
| Release archives | Release assets | **Downloads** section | Tag archive endpoint (no Downloads feature) |
| Catalog JSON | `raw.githubusercontent.com` | Raw file URL | Raw file URL |
| Private access | PAT (`github` provider) | Access token / API token (`bitbucket` provider) | HTTP access token (`bitbucket` provider) |

> **Version note:** Spec Kit never runs `git` to install artifacts, so
> SSH remotes are not used. Private repositories are reached with tokens
> over HTTPS (see [Private repositories](#private-repositories)). For
> SSH-only environments, clone manually and install from the local
> checkout with `specify extension add <path-to-extension> --dev`.

## Prerequisites

- A Bitbucket workspace and repository (Cloud) or project/repo (Data Center).
- Spec Kit CLI installed (`specify`).
- For private repos: a token (see [Private repositories](#private-repositories))
  and Spec Kit with the `bitbucket` auth provider.

## Step 1 — Lay out the repository

A single repository can host the catalog and the artifact sources:

```text
my-speckit-catalog/
├── catalog.json              # extension catalog (served raw)
├── bundle-catalog.json       # bundle catalog (served raw)
├── extensions/
│   └── my-extension/         # extension source (manifest, commands, …)
└── bundles/
    └── my-bundle/            # bundle source
```

Authoring the artifacts themselves is out of scope here — see the
[Extension Development Guide](https://github.com/github/spec-kit/blob/main/extensions/EXTENSION-DEVELOPMENT-GUIDE.md)
and the [Bundles reference](../reference/bundles.md)
(`specify bundle init` / `specify bundle build`).

## Step 2 — Build and upload release archives

Package each release as a `.zip` or `.tar.gz` and record its SHA-256:

```bash
tar -czf my-extension-1.0.0.tar.gz -C extensions my-extension
sha256sum my-extension-1.0.0.tar.gz
```

### Bitbucket Cloud: upload to the Downloads section

Via the web UI (**Repository settings → Downloads**), or via the API:

```bash
curl -X POST \
  -H "Authorization: Bearer $BITBUCKET_ACCESS_TOKEN" \
  -F files=@my-extension-1.0.0.tar.gz \
  "https://api.bitbucket.org/2.0/repositories/<workspace>/<repo>/downloads"
```

The uploaded file is then reachable at two equivalent URLs:

- Web: `https://bitbucket.org/<workspace>/<repo>/downloads/my-extension-1.0.0.tar.gz`
- API: `https://api.bitbucket.org/2.0/repositories/<workspace>/<repo>/downloads/my-extension-1.0.0.tar.gz`

**Use the API form in catalogs.** It works for public and private repos
alike (the web form does not reliably accept token auth on private
repos). Both return a redirect to a pre-signed Amazon S3 URL — that is
expected and handled (see the redirect note under
[Private repositories](#private-repositories)).

Upload a **new filename per version** (Downloads files are mutable in
place; versioned names keep old catalog entries working and make the
`sha256` meaningful).

### Alternative: tag archives

Bitbucket Cloud can serve an archive of any tag directly:

```text
https://bitbucket.org/<workspace>/<repo>/get/v1.0.0.tar.gz
```

This skips the upload step, but archive bytes are generated on the fly
and are not guaranteed stable across Bitbucket infrastructure changes —
a pinned `sha256` can go stale, and the archive contains the whole
repository rather than one packaged artifact. Prefer uploaded Downloads
artifacts for anything you publish to other people.

## Step 3 — Author the catalogs

### Extension catalog (`catalog.json`)

```json
{
  "schema_version": "1.0",
  "updated_at": "2026-08-27T00:00:00Z",
  "catalog_url": "https://bitbucket.org/<workspace>/<repo>/raw/main/catalog.json",
  "extensions": {
    "my-extension": {
      "id": "my-extension",
      "name": "My Extension",
      "version": "1.0.0",
      "description": "What it does.",
      "author": "Your Team",
      "license": "MIT",
      "repository": "https://bitbucket.org/<workspace>/<repo>",
      "download_url": "https://api.bitbucket.org/2.0/repositories/<workspace>/<repo>/downloads/my-extension-1.0.0.tar.gz",
      "sha256": "<sha256 of the archive>",
      "requires": { "speckit_version": ">=0.14.0" },
      "tags": ["internal"]
    }
  }
}
```

### Bundle catalog (`bundle-catalog.json`)

```json
{
  "schema_version": "1.0",
  "updated_at": "2026-08-27T00:00:00Z",
  "catalog_url": "https://bitbucket.org/<workspace>/<repo>/raw/main/bundle-catalog.json",
  "bundles": {
    "my-bundle": {
      "id": "my-bundle",
      "name": "My Team Bundle",
      "version": "1.0.0",
      "role": "developer",
      "description": "Curated stack for our team.",
      "author": "Your Team",
      "license": "MIT",
      "repository": "https://bitbucket.org/<workspace>/<repo>",
      "download_url": "https://api.bitbucket.org/2.0/repositories/<workspace>/<repo>/downloads/my-bundle-1.0.0.zip",
      "sha256": "<sha256 of the archive>",
      "requires": { "speckit_version": ">=0.14.0" },
      "provides": { "extensions": 1, "presets": 2, "steps": 0, "workflows": 0 },
      "tags": ["internal"],
      "verified": false
    }
  }
}
```

Rules that apply to both:

- Every `download_url` must be HTTPS. Always set `sha256` — the download
  path verifies it, which matters doubly on Bitbucket because the final
  hop is a pre-signed S3 URL.
- The map key must equal the entry's `id`.
- Commit the catalogs and note their **raw** URLs, e.g.
  `https://bitbucket.org/<workspace>/<repo>/raw/main/catalog.json`.

## Step 4 — Register the catalogs in a project

In a Spec Kit project:

```bash
specify extension catalog add \
  "https://bitbucket.org/<workspace>/<repo>/raw/main/catalog.json" \
  --name my-bitbucket --priority 10 --install-allowed
```

```bash
specify bundle catalog add \
  "https://bitbucket.org/<workspace>/<repo>/raw/main/bundle-catalog.json" \
  --id my-bitbucket --priority 10 --policy install-allowed
```

Only pass `--install-allowed` / `--policy install-allowed` for catalogs
you own and vet; without it the source is search-only. The commands
persist to `.specify/extension-catalogs.yml` and
`.specify/bundle-catalogs.yml`, so commit those files to share the
sources with your team.

## Step 5 — Install

```bash
specify extension add my-extension
specify bundle install my-bundle
```

One-off installs without a catalog also work — any HTTPS URL is accepted:

```bash
specify extension add my-extension \
  --from "https://api.bitbucket.org/2.0/repositories/<workspace>/<repo>/downloads/my-extension-1.0.0.tar.gz"
```

```bash
specify preset add \
  --from "https://api.bitbucket.org/2.0/repositories/<workspace>/<repo>/downloads/my-preset-1.0.0.zip"
```

Workflow overlay URLs follow the same rules (HTTPS + optional `auth.json`
credentials).

## Private repositories

Authentication is opt-in via `~/.specify/auth.json` — see the
[Authentication reference](../reference/authentication.md). Two options:

**Access token (recommended).** Create a repository, project, or
workspace access token with the **Repositories: Read** scope
(*Repository settings → Security → Access tokens*):

```json
{
  "providers": [
    {
      "hosts": ["api.bitbucket.org", "bitbucket.org"],
      "provider": "bitbucket",
      "auth": "bearer",
      "token_env": "BITBUCKET_ACCESS_TOKEN"
    }
  ]
}
```

**Atlassian API token (Basic auth).** Tied to a user account
(`id.atlassian.com` → API tokens); the username is the Atlassian account
email:

```json
{
  "providers": [
    {
      "hosts": ["api.bitbucket.org", "bitbucket.org"],
      "provider": "bitbucket",
      "auth": "basic",
      "username": "you@example.com",
      "token_env": "ATLASSIAN_API_TOKEN"
    }
  ]
}
```

Then restrict the file and export the token:

```bash
chmod 600 ~/.specify/auth.json
```

Notes for private setups:

- Use `api.bitbucket.org/2.0/.../downloads/<file>` URLs for archives and
  `bitbucket.org/.../raw/...` URLs for catalogs, and list **both** hosts
  in the entry.
- Downloads redirect to a pre-signed S3 URL. Spec Kit strips the
  `Authorization` header on that redirect (the target leaves your
  declared hosts) — expected behavior; the S3 URL authorizes itself and
  `sha256` covers integrity.
- In CI, provide the token via a secured pipeline variable and write
  `auth.json` in a setup step.

## Bitbucket Data Center / Server

Data Center has no Downloads section; use raw files for catalogs and the
archive endpoint (or an artifact store you already run) for archives:

- Catalog: `https://bitbucket.example.com/projects/<KEY>/repos/<slug>/raw/catalog.json?at=refs/heads/main`
- Tag archive: `https://bitbucket.example.com/rest/api/latest/projects/<KEY>/repos/<slug>/archive?at=refs/tags/v1.0.0&format=tar.gz`

Authenticate with an HTTP access token (repo → *Settings → HTTP access
tokens*, `Repository read` permission):

```json
{
  "hosts": ["bitbucket.example.com"],
  "provider": "bitbucket",
  "auth": "bearer",
  "token_env": "BITBUCKET_DC_TOKEN"
}
```

## CI: publish on tag with Bitbucket Pipelines

```yaml
pipelines:
  tags:
    'v*':
      - step:
          name: Publish Spec Kit extension archive
          script:
            - VERSION=${BITBUCKET_TAG#v}
            - tar -czf "my-extension-${VERSION}.tar.gz" -C extensions my-extension
            - sha256sum "my-extension-${VERSION}.tar.gz"
            - >
              curl -sf -X POST
              -H "Authorization: Bearer ${PUBLISH_TOKEN}"
              -F files=@"my-extension-${VERSION}.tar.gz"
              "https://api.bitbucket.org/2.0/repositories/${BITBUCKET_WORKSPACE}/${BITBUCKET_REPO_SLUG}/downloads"
```

After uploading, update the catalog entry's `version`, `download_url`,
and `sha256` (commit to the branch your raw catalog URL points at).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `401`/`403` fetching catalog or archive | No matching `auth.json` entry (check both `bitbucket.org` and `api.bitbucket.org` are listed), token expired, or missing **Repositories: Read** scope. |
| `unknown provider 'bitbucket'` when loading `auth.json` | Spec Kit version predates the Bitbucket provider — upgrade the CLI. |
| `auth='basic' requires 'username'` | The `basic` scheme needs the `username` field (Atlassian account email for API tokens). |
| `download URL must use HTTPS` | Catalog entry uses `http://` or a non-URL — only HTTPS (or loopback HTTP for local testing) is accepted. |
| SHA-256 mismatch on install | Catalog `sha256` not updated after re-uploading the archive, or the Downloads file was replaced in place. Upload versioned filenames and refresh the catalog. |
| Download works in browser but not via CLI on a private repo | The catalog uses the `bitbucket.org/.../downloads/...` web URL; switch `download_url` to the `api.bitbucket.org/2.0/.../downloads/...` form. |
| Stale search results after updating the catalog | Catalogs are cached for 1 hour. Delete `.specify/extensions/.cache/` to force a refetch. |
