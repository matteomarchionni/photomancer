# Configuration

PhotoMancer keeps its settings in one commented YAML file, `config.yaml`, and its
vocabulary in `taxonomy.yaml`. The panel edits the common settings (**Options**);
everything else can be edited by hand (**Options → Model and folders → Edit config.yaml /
Edit taxonomy**). The scheduled run reads the same files.

## Where the files are

| | Installed app | Source checkout |
|---|---|---|
| `config.yaml`, `taxonomy.yaml`, `theme.css` | Windows `%APPDATA%\PhotoMancer`, macOS `~/Library/Application Support/PhotoMancer`, Linux `~/.config/photomancer` | next to the code, if a `config.yaml` is there ("portable" mode) |
| data (catalog, previews, reports, logs) | `data_dir`, chosen at first run (default `Documents/PhotoMancer data`) | same |
| panel preferences (`gui_settings.json`) | Windows `%LOCALAPPDATA%\PhotoMancer`, macOS as above, Linux `~/.local/state/photomancer` | same |

Inside the data folder: `catalog.db` (the SQLite catalogue), `cache/img` and
`cache/thumb` (1024 px previews and thumbnails — about 1 GB per 5,000 photos), `logs/`,
`reports/` (reports, contact sheets, selections) and `export/` when there is no backup
folder.

`PHOTOMANCER_CONFIG=<file>` points to another `config.yaml`; `PHOTOMANCER_ROOT` and
`PHOTOMANCER_DATA` override the archive and the data folder for one run (handy for tests).

## Main settings

| Key | Default | Meaning |
|---|---|---|
| `photo_root` | – | Your archive. Only ever read; the sidecars are the only files written next to the photos. |
| `data_dir` | `Documents/PhotoMancer data` | **Use a local disk**: SQLite on a cloud-drive mount can be locked by the sync client mid-run. |
| `backup_dir` | `''` | A synced folder that receives the catalogue backup and the export for AI agents after every run. If it is inside `photo_root`, its folder name must be in `exclude_dirs`. |
| `exclude_dirs` | `photomancer_data, photomancer_backup, …` | Folder names skipped anywhere in the archive (case-insensitive). |
| `taxonomy_file` | `taxonomy.yaml` | The vocabulary (below). |
| `prompt_file` | `''` | A custom prompt; `{taxonomy}` marks where the vocabulary goes. Empty = built-in. |
| `lmstudio.model` | `qwen/qwen3.5-9b` | A **vision** chat model in LM Studio. |
| `lmstudio.context_per_request` | `4096` | The model is loaded with this × `vlm.concurrency` (a photo needs ~3.4k tokens). |
| `lmstudio.reasoning_effort` | `none` | Reasoning off: ~3 s per photo instead of minutes. |
| `lmstudio.embedding_model` | `text-embedding-nomic-embed-text-v1.5` | For search by meaning. |
| `lmstudio.base_url` | `http://127.0.0.1:1234/v1` | Use `127.0.0.1`, not `localhost` (on Windows `localhost` adds ~2 s to every request). |
| `vlm.image_side` | `1024` | Long side of the image sent to the model (768 is faster, 1024 sees more). |
| `vlm.exif_context` | `true` | Tell the model the camera, exposure and date (helps with long exposures, night, telephoto). |
| `vlm.concurrency` | `2` | Photos in flight at once (1–4). Benchmark before raising. |
| `vlm.max_attempts` | `3` | Runs after which a failing photo is given up. |
| `preprocess.workers` | CPU cores − 2 | Processes that decode, resize and analyse photos. |

Advanced blocks (`exif`, `colors`, `dedup`, `xmp`, `report`) are commented in the file:
thresholds for "long exposure", "high ISO" and "telephoto"; the colour clustering; the
near-duplicate threshold; the XMP tag root (`AI`), how many colours become tags, and
whether keywords are also written as `AI|Keyword|…` tags (Immich shows tags, not flat
keywords).

## Results are stored per configuration

Every result records the model, the prompt, the taxonomy (version and checksum) and the
image settings. Change any of them and PhotoMancer treats the photos as *to do* with the
new configuration — old results are kept, never mixed with new ones. **Rebuild from
scratch** re-tags everything at once; otherwise *Tag new photos* catches up gradually.

## The taxonomy

`taxonomy.yaml` is the controlled vocabulary the model must choose from; its answer is
validated against it. The first run copies the default next to `config.yaml`.

```yaml
taxonomy_version: "1.0.0"          # bump it on any change
genres:                            # exactly one genre and one subgenre per photo
  landscape:
    description: "Natural scenery is the subject; people, if any, are small elements."
    subgenres: [mountain, coastal_seascape, lake, forest_woodland, …, other_landscape]
  …
decision_rules:                    # sent to the model verbatim
  - "Choose the genre of the MAIN SUBJECT, not of the location."
facets:                            # independent of genre
  time_of_day: [day, golden_hour, blue_hour, sunrise_sunset, twilight, night, unknown]
  techniques: [long_exposure, black_and_white, panorama, …, none]
color_palette:                     # named colours for the dominant-colour tags
  sky_blue: "#87CEEB"
```

Tips: put your main genres first; keep an `other_…` subgenre in every genre; write
decision rules for the pairs the model confuses (see the report's "disagreements").

## The look: `theme.css`

**Options → Appearance → Edit theme** — colours, fonts, font size and transparency, one
commented line each. Save, then **Apply**; **Reset to default** rewrites the file.
