# Production template source archive

This folder preserves the exact ZIP for every published Voomi template version. Archives were recovered from the repository?s GitHub Releases and verified against each release metadata sidecar SHA-256 plus the embedded `templateId` and `templateVersion`.

## Layout

```text
Production/<templateId>/<templateVersion>/template.zip
```

`index.json` lists each archived version, its source release tag, checksum, and GitHub release URL. Keep older version folders. To prepare an update, copy the desired ZIP from this archive into `incoming/`, edit the existing template while keeping its `templateId`, increment `templateVersion`, then submit the new ZIP. After the release succeeds, CI moves that ZIP into its new version folder under `Production/`.
