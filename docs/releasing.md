# Releasing litdown to PyPI

Publishing is automated by [`.github/workflows/release.yml`](../.github/workflows/release.yml)
via PyPI **Trusted Publishing** (OIDC): CI mints a short-lived, scoped token at
publish time, so there is no PyPI API token stored in the repo.

## Cut a release

1. Bump `version` in `pyproject.toml` and merge it to `main`.
2. Publish a GitHub Release whose tag is `v<version>` (e.g. `v0.3.1`).

The `release` workflow then checks the tag matches the `pyproject.toml`
version (a published version is irreversible), builds the sdist + wheel with
`uv build`, and publishes them. The `publish` job runs in the `pypi`
environment and holds only `id-token: write`.

## One-time setup

Both values must match the workflow exactly, or the OIDC exchange is rejected:
workflow filename `release.yml`, environment `pypi`.

- **PyPI trusted publisher** — on the account that owns the project, add a
  publisher (a *pending* publisher if the project doesn't exist yet):
  - Project: `litdown`
  - Owner: `populationgenomics`
  - Repository: `litdown`
  - Workflow: `release.yml`
  - Environment: `pypi`
- **GitHub environment** — create an environment named `pypi` (Settings →
  Environments). Add required reviewers there if a manual publish gate is
  wanted.

## Ownership

Pending publishers and first uploads attach to a **user** account, not an
organization, so the project is first created under a personal PyPI account
and then transferred into the CPG organization. This needs no CI change:
trusted publishing keys off the GitHub repository (`populationgenomics/litdown`),
which is independent of who owns the PyPI project.
