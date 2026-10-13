# Packaging Voomi templates

Contributors submit packages under `incoming/` as `<templateId>-v<templateVersion>.zip`. IDs may use letters, numbers, hyphens, or underscores in `template.json`; publication normalizes uppercase and underscores to lowercase hyphens. For example, `Voomi_POP_Comic_Star_Rebuild` becomes `voomi-pop-comic-star-rebuild`. The ZIP may be named `Voomi_POP_Comic_Star_Rebuild_v0.2.0.zip` or with the canonical name `voomi-pop-comic-star-rebuild-v0.2.0.zip`. Versions use SemVer. Never change bytes of an already released ID/version; publish a new version. Include only assets that may be redistributed, with complete license evidence.

## Required archive layout

```text
template.json
README.md
LICENSES.md
placeholders/                 # may be empty
assets/                       # may be empty
audio/                        # may be empty
preview/
  poster.jpg                  # 1080 x 1920 JPEG
  thumbnail.jpg               # 720 x 1280 JPEG
  preview.mp4                 # H.264, portrait 9:16, >=720 x 1280, 30 FPS
licenses/
  asset-manifest.json
```

Empty optional asset directories can be omitted. Preview files and the metadata and license files are required. `template.json` must satisfy `schemas/template.schema.json` and use `schemaVersion: "1.0.0"`. It declares metadata, supported renderer features, media slots, scenes, preview paths, and `licenseManifest: "licenses/asset-manifest.json"`. Preview video duration must match `durationMs` within one 30 FPS frame plus 5 ms. Scene timing uses the pipeline's contiguous, ordered timeline policy when all scenes have explicit `startMs` and `durationMs`.

Media slot types are `photo`, `video`, `photo_or_video`, and `none`. Scene IDs and slot IDs must be unique. A scene can refer to zero, one, or multiple declared slots using `mediaSlotIds` (or legacy `mediaSlots` array). Required asset references must point to files in the ZIP. Do not add executable files, symlinks, encrypted ZIP entries, or paths that escape the package.

## License manifest

`licenses/asset-manifest.json` has an `assets` array. Include one record for each distributed file under `assets/`, `audio/`, and `placeholders/`:

```json
{
  "assets": [
    {
      "path": "assets/background.jpg",
      "creator": "Creator name",
      "sourceUrl": "https://example.org/source",
      "license": "CC-BY-4.0",
      "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
      "attribution": "Creator, source, CC BY 4.0",
      "redistributionApproved": true
    }
  ]
}
```

Allowed license identifiers are maintained in `scripts/validate_template.py`. A declaration must be backed by evidence that permits redistribution. The checker confirms fields and allowlist membership; it cannot establish legal permission. Ask for review when a license or source is unclear.

## Validate before upload

```bash
python -m pip install -r requirements.txt
python scripts/validate_template.py --zip incoming/money-rain-v1.0.0.zip --report validation-report.json
python -m pytest tests/ -v
```

The repository currently has no app renderer schema or sample template. Confirm renderer features and scene timing against the app before relying on a template in production.
