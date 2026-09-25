# 1. Install and first run

## LM Studio and the models

PhotoMancer uses [LM Studio](https://lmstudio.ai) (free) to run the models on your own
computer.

1. Install LM Studio and open it once.
2. In **Discover**, download:
   - a **vision** chat model — the default is **`qwen/qwen3.5-9b`** (about 8 GB;
     comfortable on a GPU with 12 GB of memory);
   - the embedding model **`nomic-embed-text-v1.5`** (84 MB), for search by meaning.

You do not need to load them or start the server: PhotoMancer does it before every run.

## Install PhotoMancer

- **Windows**: run `PhotoMancer-<version>-Setup.exe` (no administrator rights). Tick
  *Create a desktop icon* if you want one; the Start-menu entry is on by default.
- **macOS**: open the `.dmg`, drag PhotoMancer to Applications; first time, right-click →
  Open (the build is not signed by Apple).
- **Linux**: `chmod +x` the AppImage and run it.
- **Python**: `pipx install git+https://github.com/matteomarchionni/photomancer`, then
  `photomancer`.

## The setup window

On first launch PhotoMancer asks for:

| Folder | What for |
|---|---|
| **Your photo archive** | The folder with your JPEGs (any depth). Only ever read. |
| **Data folder** | Catalogue, previews, reports. About 1 GB per 5,000 photos. Keep it on a local disk, not in a cloud-drive folder. |
| **Backup and export folder** (optional) | Receives a backup of the catalogue and the export for AI agents after every run. A synced folder is ideal. |

It also checks LM Studio and the model, and offers a desktop icon and a menu entry
(unless the Windows installer already asked). Press **Start**.

![Overview](../images/overview.png)

## A tour of the panel

- **Overview** — tiles (JPEGs in the archive, tagged, to do, given up), the **Run** card,
  the **Status** card with progress bars, and the activity log.
- **Browse**, **Folders**, **Curate** — tutorials 4 and 5.
- **Options** — maintenance (report, check setup, remove sidecars, rebuild…), tagging
  options, model and folders, appearance.
- The sidebar's **Folders** section opens the archive, reports, backup, logs and data
  folders.

Next: [tag your archive](02-tag-your-archive.md).
