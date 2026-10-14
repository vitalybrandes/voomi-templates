# Packaging Voomi templates

Contributors submit packages under `incoming/` as ZIPs. Versions use SemVer. Never change bytes of an already released ID/version; publish a new version. The pipeline accepts both the catalog distribution layout below and the Voomi app-native export layout described at the end of this document. During publishing, the package metadata is normalized to include a stable UUID in `id`; the resulting ZIP is the one published.

## Required archive layout

```text
template.json
README.md
placeholders/                 # may be empty
assets/                       # may be empty
audio/                        # may be empty
preview/
  preview.jpg                 # 1080 x 1920 JPEG
licenses/
  asset-manifest.json
```

Empty optional asset directories can be omitted. This distribution layout uses one JPEG preview image and a machine-readable asset manifest. `LICENSES.md` is not required. `template.json` uses `schemaVersion: "1.0.0"`, a readable `templateId`, and a UUID `id` (older packages are upgraded during publishing). Scene timing uses the pipeline's contiguous, ordered timeline policy when all scenes have explicit `startMs` and `durationMs`.

## Voomi app-native ZIPs

App-native ZIPs can use a namespaced `templateId` such as `com.voomi.samples.neon-dance-beat-drop`, the app's renderer feature names, and a single PNG or JPEG declared directly in `preview` (or as `preview.preview`). Their metadata must include a stable UUID `id`, readable `templateId`, SemVer versions, name, description, category, duration, fps, resolution, renderer features, media slots, scenes, and preview image path. Referenced placeholders, scene image layers, audio files, and preview images must exist in the archive. The single app preview image is published in the catalog. `licenses/asset-manifest.json` is not required or interpreted for this app-native format. Existing ZIPs without UUID `id` are normalized during publishing.

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
