# Submit a Voomi template

The template generator should produce the ZIP and composition preview. Submit generated Schema 2.0 or 3.0 packages in `incoming/`; CI validates, publishes, and catalogs them.

## ZIP layout

```text
template.zip
├── template.json
├── preview.jpg
└── assets/
    ├── backgrounds/
    ├── images/
    ├── stickers/
    ├── frames/
    ├── animations/
    ├── overlays/
    ├── audio/
    └── fonts/
```

Empty asset directories may be omitted. No other asset folders are allowed. All file references must resolve inside the ZIP. Include only one preview image: root `preview.jpg`. Do not include `poster.jpg`, `thumbnail.jpg`, or `preview.mp4`.

## Metadata

Use [schemas/template.schema.json](schemas/template.schema.json) for Schema 2.0 and 3.0 packages. Schema 2.0 requires `schemaVersion: "2.0"`; Schema 3.0 requires `schemaVersion: "3.0"` plus `engine.requiredCapabilities`. Both versions use the required fields below:

- `schemaVersion`: `"2.0"` or `"3.0"`
- `templateId`: UUID v4
- `templateVersion`: SemVer
- `name`, `description`, `durationMs`, `canvas`, `preview`, `mediaSlots`, and `layers`

Optional fields include `stages`, `categories`, `tags`, and `requiredFeatures`. Do not add `templateVersionId`. A version is uniquely identified by `(templateId, templateVersion)`.

Layer intervals use milliseconds and must fit inside `durationMs`. `userMedia` layers must reference a declared `mediaSlots[].id` via `slotId`; other visual/audio layers reference files with `source`. Use normalized layout coordinates. Stages can be sequential and visual layers may overlap. Declare required renderer features.

The JPEG preview should be rendered from the actual layer composition with temporary sample media so it represents the template as users will see it.

## Local checks

```bash
python -m pip install -r requirements-dev.txt
python scripts/validate_template.py --zip incoming/template.zip
python -m pytest tests/ -v
```

CI publishes the ZIP as `template.zip` under `template-{templateId}-v{templateVersion}`. Published versions are immutable. Retry identical bytes to recover an interrupted run; change the version when content changes.


## Schema 3.0 motion packages

Schema 2.0 uses keyframe animations. Schema 3.0 uses `engine.requiredCapabilities` and per-layer `motions` with a motion ID, `startMs`, `durationMs`, and optional `params`. Do not mix the two animation formats in one package. The intake validator checks motion IDs, supported parameter names, capability declarations, and layer timing. Pre-rendered overlays remain media assets, not motion IDs. Catalog entries retain the template schema version and Schema 3.0 engine capabilities.
