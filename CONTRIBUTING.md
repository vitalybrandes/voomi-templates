# Packaging Voomi templates

Contributors submit packages under `incoming/` as ZIPs. Versions use SemVer. Never change bytes of an already released ID/version; publish a new version. The pipeline accepts both the catalog distribution layout below and the Voomi app-native export layout described at the end of this document. The original ZIP is published unchanged.

## Required archive layout

```text
template.json
README.md
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

Empty optional asset directories can be omitted. This distribution layout uses JPEG poster and thumbnail files and a machine-readable asset manifest. `LICENSES.md` is not required. `template.json` uses `schemaVersion: "1.0.0"`. The pipeline checks that `preview.mp4` exists but does not inspect its streams, dimensions, frame rate, audio, or duration. Scene timing uses the pipeline's contiguous, ordered timeline policy when all scenes have explicit `startMs` and `durationMs`.

## Voomi app-native ZIPs

App-native ZIPs are accepted unchanged. They can use a namespaced ID such as `com.voomi.samples.neon-dance-beat-drop`, the app's renderer feature names, and a single PNG declared by `preview.poster` (for example `preview/preview.png`). Their metadata must include the ID, SemVer versions, name, description, category, duration, fps, resolution, renderer features, media slots, scenes, and preview path. Referenced placeholders, scene image layers, audio files, and preview images must exist in the archive. For catalog display, the single app preview image is used for both poster and thumbnail. `licenses/asset-manifest.json` is not required or interpreted for this app-native format.

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
