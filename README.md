# Voomi template distribution

This repository distributes immutable template ZIPs through public GitHub Releases and exposes the current catalog at `catalog.json`. GitHub Actions validate packages, publish versioned releases, verify uploaded bytes, extract versioned preview images, and commit the catalog and release metadata index. No application server or paid storage is used.

## Architecture and current contract

`incoming/` is a short lived intake directory. The publishing workflow validates every new ZIP, creates a release named `template-<id>-v<version>` containing the ZIP and a JSON metadata sidecar, verifies the downloadable ZIP's byte count and SHA-256, writes validated release metadata to `release-index.json`, extracts only poster and thumbnail images into `previews/<id>/<version>/`, rebuilds `catalog.json`, then commits generated files and removes successfully processed ZIPs. Earlier release assets and versioned previews remain available.

The repository was initially empty apart from its README and did not include the Voomi Flutter application, a template package, or an existing renderer schema. This implementation therefore uses the metadata shape described in the project brief as schema version `1.0.0`. The renderer feature allowlist and timeline rule are explicit policy in `scripts/validate_template.py`: scenes with explicit starts and durations must be ordered, contiguous, and end within 50 ms of `durationMs`. Confirm these points against the Android renderer before shipping production templates. Nothing here proves that a package renders correctly in the app.

## Repository setup

The repository must be public for anonymous catalog and release downloads. Enable GitHub Actions and allow Actions to create and approve pull requests only if your branch policy needs it; this workflow pushes generated commits directly to `main`, so `main` branch protection must permit GitHub Actions bot pushes (or use a repository ruleset exception for `github-actions[bot]`). Keep the built in `GITHUB_TOKEN` limited to `contents: write` for publishing and catalog jobs, and `contents: read` for validation. Do not run publishing on pull requests. The Actions are pinned to reviewed commit SHAs.

The workflow serializes publication with `voomi-template-publishing`. Set repository Actions permissions so the `GITHUB_TOKEN` can write repository contents and create releases. No personal access token is required. The repository owner must review each license declaration; automation checks completeness and an allowlist, not the legal truth of the declaration.

## Upload and publish a template

Prepare a ZIP that follows [CONTRIBUTING.md](CONTRIBUTING.md), add it under `incoming/`, then commit and push it to `main`. Uppercase and underscores in the metadata ID are normalized to lowercase hyphens for release and catalog IDs; the ZIP can use either its source ID filename or canonical filename. A push containing multiple ZIPs processes each package. Failed packages stay in `incoming/` and get an error report; successfully published packages are removed in the generated commit. A failed git commit or push leaves the source package for recovery.

For manual retry, keep the ZIP in the repository and run:

```bash
gh workflow run publish-template.yml --ref main -f zip_path=incoming/money-rain-v1.0.0.zip
gh run list --workflow publish-template.yml
gh run watch
```

Inspect failures in the Actions run logs and download the `voomi-publishing-report-*` artifact. The validation report is emitted for individual validation commands and is uploaded with the publishing artifact when present. Fix the package, increment the template version if its content changes, and retry. Identical ID/version and checksum is idempotent; the same ID/version with different bytes is rejected. Older versions may be released for existing projects, but the catalog remains on the highest SemVer version. Drafts and prereleases are excluded.

## Local commands

Install Python 3.12 and GitHub CLI. Preview MP4 validation in the pipeline checks that the file is present and named correctly; the workflow does not decode or probe video streams.

```bash
python -m pip install -r requirements.txt
gh auth login
python scripts/validate_template.py --zip incoming/money-rain-v1.0.0.zip --report validation-report.json
python scripts/extract_preview.py --zip incoming/money-rain-v1.0.0.zip --output previews/money-rain/1.0.0/
python scripts/rebuild_catalog.py
python scripts/verify_catalog.py
python -m pytest tests/ -v
```

`python scripts/publish_template.py --dry-run` validates and prints the planned release metadata without contacting GitHub or deleting input files. `python scripts/rebuild_catalog.py --dry-run` writes `catalog.preview.json`. Catalog verification checks GitHub release assets by default; use `--skip-remote` for local structural checks.

To inspect releases or download one:

```bash
gh release list
gh release view template-money-rain-v1.0.0
gh release download template-money-rain-v1.0.0 --pattern '*.zip' --dir ./downloads
```

## Catalog and Flutter contract

Catalog URL:

```text
https://raw.githubusercontent.com/vitalybrandes/voomi-templates/main/catalog.json
```

The catalog contains one current entry per template ID, ordered by ID. Each item includes the latest SemVer release, package SHA-256 and byte count, minimum app version, media slots, versioned poster and thumbnail URLs, and a public release download URL. `catalogVersion` changes only when the catalog content changes; `updatedAt` remains stable otherwise. GitHub's raw content cache can delay visibility briefly.

The Flutter app should display its cached catalog immediately, refresh remotely at launch and when the Templates page cache is older than 15 minutes, and support pull to refresh. Validate the catalog schema, compare catalog and template versions, update local metadata, and keep the last good copy on network failure. Download ZIPs only on user request, verify SHA-256 before safe extraction, check renderer and minimum app compatibility, install to versioned local storage, and preserve project references to prior versions. Public reads need no GitHub authentication. Use conditional HTTP requests when supported and retry later if GitHub caching delays a new catalog.

## Catalog recovery and source of truth

`release-index.json` is the validated metadata sidecar committed with the catalog; the identical per-release metadata sidecar is attached as `<tag>.metadata.json`. The index is maintained only after an uploaded release asset has been downloaded back and checksum-verified. The workflow commits catalog, previews, index, and intake cleanup together. If a run fails after creating a release but before the commit, retry the same package: publication checks the immutable release and completes metadata/catalog work without uploading a replacement. A scheduled/manual rebuild can recover missing index entries from release sidecars. Do not remove a catalog entry by deleting an incoming ZIP. Catalog rebuild chooses the newest valid indexed release and never unpublishes old release assets.

Run `gh workflow run rebuild-catalog.yml --ref main` to reconstruct the catalog from the index. A weekly scheduled run also verifies the catalog against release assets. For a catalog commit failure caused by branch protection, enable the documented bot exception or adjust the organization-approved generated-data publication policy; do not disable security checks.

## Security and limitations

ZIPs are untrusted. The validator rejects traversal, absolute and duplicate normalized names, symlinks, encryption, executable files, corrupt archives, excessive entry counts and configured compressed/uncompressed limits. It never executes package files and extracts only named preview images after validation. Default limits are 250 MiB compressed and 1 GiB expanded; override with `VOOMI_MAX_ZIP_BYTES`, `VOOMI_MAX_UNCOMPRESSED_BYTES`, and `VOOMI_MAX_ZIP_ENTRIES` in a controlled environment. The validator checks poster and thumbnail image contents and dimensions. It checks the preview MP4's presence but does not verify its codec, duration, audio, or decodability. License checking cannot independently verify rights. Packages with unclear redistribution rights require human review.

GitHub Releases and Actions have platform quotas and bandwidth limits. This initial setup uses public GitHub capabilities only. If distribution volume exceeds GitHub limits, migrate immutable package artifacts to an object store in a future project while retaining the catalog contract; no alternate provider is configured here. A successful live release, anonymous download, or in-app refresh cannot be claimed until a real conforming ZIP is uploaded and the published URL is checked.
