# Voomi template repository

This repository is prepared for new templates using Voomi Template Schema v2. Template ZIP generation happens separately; this repository validates, publishes, and catalogs generated v2 packages. `catalog.json` is the canonical v2 feed.

## Package contract

Follow [CONTRIBUTING.md](CONTRIBUTING.md) and [schemas/template.schema.json](schemas/template.schema.json). Each ZIP contains `template.json`, one root `preview.jpg`, and referenced assets under the approved `assets/` folders. Template identity is the pair `(templateId, templateVersion)`, where `templateId` is UUID v4 and the version is SemVer. No `templateVersionId` is used.

## Publish

Place generated ZIPs in `incoming/` and merge them to `main`. The workflow validates the ZIP, creates an immutable release tagged `template-{templateId}-v{templateVersion}`, uploads it as `template.zip`, verifies its SHA-256, extracts the exact `preview.jpg`, and rebuilds the catalogs. Identical retries are safe. A package with different bytes for an already published UUID/version is rejected; publish content changes under a new version.

Local validation:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -v
python scripts/validate_template.py --zip incoming/template.zip
python scripts/publish_template.py --dry-run
python scripts/rebuild_catalog.py
python scripts/verify_catalog.py --skip-remote
```

## Catalogs

`catalog.json` is the only public catalog and contains only v2 templates. Rebuild chooses each template's latest version by SemVer and keeps every immutable release downloadable.

## Generated data and historical releases

Incoming ZIPs, public preview images, catalog files, release-index entries, and validation/publishing reports are generated data. This checkout has been reset for the new v2 template set. The publishing workflow does not delete GitHub Releases; historical releases must be managed separately. This repository migration leaves renderer source and template-generation tooling untouched because they are not part of this repository.

## Security limits

The validator rejects unsafe ZIP paths, symlinks, encrypted and executable files, excessive package sizes, unsupported asset folders, missing references, malformed timelines, unsupported animations, invalid chroma-key settings, and malformed previews. It does not execute package files. The repository does not contain the Voomi renderer, so successful validation cannot guarantee renderer compatibility or preview fidelity.
