# Voomi template distribution

This repository distributes immutable template ZIPs through public GitHub Releases and exposes the current catalog at `catalog.json`. GitHub Actions validate packages, publish versioned releases, verify uploaded bytes, extract versioned preview images, and commit the catalog and release metadata index. No application server or paid storage is used.

## Architecture and current contract

`incoming/` is a short lived intake directory. The publishing workflow validates every new ZIP, creates a release named `template-<id>-v<version>` containing the ZIP and a JSON metadata sidecar, verifies the downloadable ZIP's byte count and SHA-256, writes validated release metadata to `release-index.json`, extracts the declared preview image into `previews/<id>/<version>/`, rebuilds `catalog.json`, then commits generated files and removes successfully processed ZIPs. Earlier release assets and versioned previews remain available.

The repository accepts catalog distribution metadata and Voomi app-native ZIP exports. Each package uses `id` as its stable UUID and `templateId` as its readable package key. Older ZIPs are upgraded in place during publishing, retaining their existing UUID when known or assigning a stable UUID. The declared preview image is published at the catalog `previewUrl`. The validator checks required metadata and referenced files, but does not guarantee renderer compatibility or prove that a package renders correctly in the app.

## Repository setup

The repository must be public for anonymous catalog and release downloads. Enable GitHub Actions and allow Actions to create and approve pull requests only if your branch policy needs it; this workflow pushes generated commits directly to `main`, so `main` branch protection must permit GitHub Actions bot pushes (or use a repository ruleset exception for `github-actions[bot]`). Keep the built in `GITHUB_TOKEN` limited to `contents: write` for publishing and catalog jobs, and `contents: read` for validation. Do not run publishing on pull requests. The Actions are pinned to reviewed commit SHAs.

The workflow serializes publication with `voomi-template-publishing`. Set repository Actions permissions so the `GITHUB_TOKEN` can write repository contents and create releases. No personal access token is required. Distribution-format packages can carry a machine-readable license manifest; app-native packages do not require one.

## Upload and publish a template

Prepare a ZIP that follows [CONTRIBUTING.md](CONTRIBUTING.md), add it under `incoming/`, then commit and push it to `main`. Uppercase and underscores in the metadata ID are normalized to lowercase hyphens for release and catalog IDs; the ZIP can use either its source ID filename or canonical filename. A push containing multiple ZIPs processes every newly added or modified package independently. Valid packages are published and removed in the generated commit; invalid ZIPs remain in `incoming/`, receive warning annotations and appear in the Actions run summary and report artifact. Package validation failures do not fail the workflow, even when every package in a batch is rejected. Systemic publishing, catalog verification, and repository commit failures still fail the run. A failed git commit or push leaves successfully published source ZIPs available for recovery.

For manual retry, keep the ZIP in the repository and run:

```bash
gh workflow run publish-template.yml --ref main -f zip_path=incoming/money-rain-v1.0.0.zip
gh run list --workflow publish-template.yml
gh run watch
```

Inspect failures in the Actions run logs and download the `voomi-publishing-report-*` artifact. The validation report is emitted for individual validation commands and is uploaded with the publishing artifact when present. Fix the package, increment the template version if its content changes, and retry. The publisher matches by UUID `id`; it publishes only a version newer than the current release. Equal or older versions are skipped and removed from intake. `name` is display text and is not used for matching. Drafts and prereleases are excluded.

## Local commands

Install Python 3.12 and GitHub CLI. Template previews are images only; the declared preview image is validated before publishing.

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

The visual catalog is implemented as a buildless static page in `site/index.html`. It reads the generated `catalog.json`, so template cards, filters, preview images, and download links stay aligned with the app catalog. The Pages workflow deploys after successful catalog publication and can also be run manually. To make the site live the first time, select **Settings â†’ Pages â†’ Build and deployment â†’ Source â†’ GitHub Actions**. Its address will be `https://vitalybrandes.github.io/voomi-templates/`.

Catalog URL:

```text
https://raw.githubusercontent.com/vitalybrandes/voomi-templates/main/catalog.json
```

The catalog contains one current entry per stable UUID, ordered by `id`. Each item includes the UUID `id`, readable `templateId`, exact manifest `sourceId`, latest SemVer version, package SHA-256 and byte count, minimum app version, media slots, a versioned `previewUrl`, and a public release download URL. Clients match packages by UUID and compare `version`; a newer package replaces the current catalog entry while older immutable release downloads remain available. `catalogVersion` changes only when the catalog content changes; `updatedAt` remains stable otherwise. GitHub's raw content cache can delay visibility briefly.

The Flutter app should display its cached catalog immediately, refresh remotely at launch and when the Templates page cache is older than 15 minutes, and support pull to refresh. Validate the catalog schema, compare catalog and template versions, update local metadata, and keep the last good copy on network failure. Download ZIPs only on user request, verify SHA-256 before safe extraction, check renderer and minimum app compatibility, install to versioned local storage, and preserve project references to prior versions. Public reads need no GitHub authentication. Use conditional HTTP requests when supported and retry later if GitHub caching delays a new catalog.

## Catalog recovery and source of truth

`release-index.json` is the validated metadata sidecar committed with the catalog; the identical per-release metadata sidecar is attached as `<tag>.metadata.json`. The index is maintained only after an uploaded release asset has been downloaded back and checksum-verified. The workflow commits catalog, previews, index, and intake cleanup together. If a run fails after creating a release but before the commit, retry the same package: publication checks the immutable release and completes metadata/catalog work without uploading a replacement. A scheduled/manual rebuild can recover missing index entries from release sidecars. Do not remove a catalog entry by deleting an incoming ZIP. Catalog rebuild chooses the newest valid indexed release and never unpublishes old release assets.

Run `gh workflow run rebuild-catalog.yml --ref main` to reconstruct the catalog from the index. A weekly scheduled run also verifies the catalog against release assets. For a catalog commit failure caused by branch protection, enable the documented bot exception or adjust the organization-approved generated-data publication policy; do not disable security checks.

## Security and limitations

ZIPs are untrusted. The validator rejects traversal, absolute and duplicate normalized names, symlinks, encryption, executable files, corrupt archives, excessive entry counts and configured compressed/uncompressed limits. It never executes package files and extracts only the declared preview image after validation. Default limits are 250 MiB compressed and 1 GiB expanded; override with `VOOMI_MAX_ZIP_BYTES`, `VOOMI_MAX_UNCOMPRESSED_BYTES`, and `VOOMI_MAX_ZIP_ENTRIES` in a controlled environment. Distribution preview images are checked for content and dimensions. App-native previews are checked as images (PNG, JPEG, or WebP). Renderer compatibility is not verified.

GitHub Releases and Actions have platform quotas and bandwidth limits. This initial setup uses public GitHub capabilities only. If distribution volume exceeds GitHub limits, migrate immutable package artifacts to an object store in a future project while retaining the catalog contract; no alternate provider is configured here. A successful live release, anonymous download, or in-app refresh cannot be claimed until a real conforming ZIP is uploaded and the published URL is checked.
