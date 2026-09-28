# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "LM Studio off" in the sidebar | Normal: *Tag new photos* starts it, and so do Browse, Folders and Curate when they need to search by meaning. If that fails, open LM Studio once by hand. **Check setup** (Options → Maintenance) tests LM Studio, the model and three real photos. |
| "Model not available" / every photo fails | The model is not installed, or has no vision. Pick a vision model under *Options → Model and folders*. Do not load it by hand in LM Studio: PhotoMancer loads it with the right context. |
| Some photos keep failing | After 3 attempts a photo is given up (the *Given up* tile; *Browse → Failed / untagged only* shows why). Tick *Retry photos that were given up* in Options. Corrupt or truncated JPEGs cannot be read. |
| A run stopped in the middle | Nothing is lost: every result is saved as it lands. Press *Tag new photos* again. |
| Tagging is slow | Check that reasoning is off (`reasoning_effort: none`), try `image_side: 768`, and `Simultaneous requests` 2–4 (benchmark: more is not always faster). ~20–28 photos/min is typical on a 12 GB GPU. |
| "Semantic index not available" / search stays on exact words | LM Studio could not be started, or the embedding model is not downloaded (the activity log says which). Install `nomic-embed-text-v1.5`; *Update search index* rebuilds it. Search falls back to exact words meanwhile. |
| A sidecar was not written ("foreign" / "edited") | On purpose: PhotoMancer never overwrites a `.jpg.xmp` written by another program or edited since. `photomancer write --force` overrides this. |
| Immich does not show the tags | Run the *Sidecar metadata* job in Immich (Administration → Jobs → Sidecar metadata → Discover / Sync) — see [tutorial 3](tutorials/03-xmp-immich-digikam.md). |
| Immich shows the backup folder as photos | Add `**/photomancer_backup/**` (your backup folder's name) to the library's exclusion patterns. |
| "backup not written: … holds the backup of another catalog" | The backup folder belongs to a different PhotoMancer data folder (a second copy or a test). Choose another folder. |
| After changing the model / prompt / taxonomy everything is "to do" | Results are stored per configuration and never mixed. Let *Tag new photos* catch up, or *Rebuild from scratch*. |
| The weekly run did not happen | The PC was off: it runs as soon as it is back on. Log: `<data>/logs/run_scheduled.log`. |
| macOS: "cannot be opened because the developer cannot be verified" | Unsigned build: right-click the app → Open, once. |
| Windows: SmartScreen "unknown publisher" | Unsigned installer: More info → Run anyway. |
| Linux: the AppImage does not start | `chmod +x` it; some distributions need `libfuse2`. |

## Removing everything PhotoMancer wrote

**Options → Maintenance → Remove AI sidecars…** deletes every `.jpg.xmp` PhotoMancer wrote
— and only those; the catalogue is kept, so *Write XMP* can bring them back. Your JPEGs
were never modified.

## Restoring on a new PC

The backup folder holds `catalog.db` (plus `catalog.prev.db`, the snapshot before),
`config.yaml`, `taxonomy.yaml` and the code. Install PhotoMancer, choose an empty data
folder at first run, copy `catalog.db` into it and your `config.yaml`/`taxonomy.yaml` to
the config folder ([configuration](configuration.md#where-the-files-are)). Previews are
rebuilt from the photos; nothing needs to be tagged again.

## Logs

**Logs** in the sidebar opens `<data>/logs`: `run_scheduled.log` (scheduled runs) and a
log per run with every photo, its time and result.
