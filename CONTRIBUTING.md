# Submit a Voomi template

The template generator should produce the ZIP and composition preview. Submit only generated Schema v2 packages in `incoming/`; CI validates, publishes, and catalogs them.

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

Use [schemas/template.schema.json](schemas/template.schema.json) and the single-stage and multi-stage examples provided for Schema v2. Required fields include:

- `schemaVersion`: `"2.0"`
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
