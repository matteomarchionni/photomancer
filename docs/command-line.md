# Command line

Every panel button is also a command, for scripts and remote sessions:

```
photomancer <command> [options]                # installed (pipx / installers: photomancer-cli)
python -m photomancer <command> [options]      # from a source checkout
```

Global options: `--root <folder>` and `--data <folder>` override `photo_root` and
`data_dir` for this run.

| Command | Does |
|---|---|
| `run` | scan, prepare and tag new photos, group near-duplicates, update the search index, export and back up. Options: `--limit N`, `--max-minutes M`, `--retry-failed`, `--concurrency N`, `--write-xmp`, `--report`, `--no-lm-setup` |
| `plan` | what a run would do — changes nothing |
| `check` | LM Studio, the model and a few real photos (`--images 3`) — changes nothing |
| `prepare` | the CPU stage only: hashes, EXIF, previews, colours (`--limit`, `--retry-failed`) |
| `write` | write XMP sidecars for tagged photos (`--dry-run`, `--limit N`; `--force` also overwrites sidecars from other programs or edited by hand) |
| `unwrite` | delete the sidecars PhotoMancer wrote, and only those (`--dry-run`, `--yes`) |
| `dedup` | recompute near-duplicate groups |
| `index` | update the semantic search index |
| `search "<query>"` | search by meaning (`--top 15`); `--folders --level 0/1/2` ranks folders instead |
| `curate "<theme>"` | the model picks the best photos for a theme (`--top 12`, `--pool 48`) |
| `report` | statistics, CSV and contact sheet in `reports/` |
| `export` | catalogue for AI agents (Markdown, JSONL, SQLite) |
| `backup` | catalogue, settings and code to `backup_dir` |
| `rebuild --yes` | back up, then re-tag every photo with the current settings (`--max-minutes`, `--write-xmp`) |
| `unschedule` | remove the weekly / monthly task (it is registered from the panel's *Repeat* choice) |
| `scheduled <command …>` | run a command with its output appended to `logs/run_scheduled.log` — what the recurring task runs |
| `shortcuts [--menu] [--desktop] [--remove]` | menu entry / desktop icon |
| `setup` | the first-run window (folders, LM Studio check, shortcuts) |
| `logdir` | print the log folder |

Examples:

```
photomancer run --limit 200 --write-xmp        # a quick batch, sidecars included
photomancer search "foggy forest at dawn" --top 30
photomancer search "wedding" --folders --level 2
photomancer write --dry-run                    # what would be written, and where
```

The exit code is 0 on success. The recurring task runs `scheduled run` (plus `--write-xmp` if ticked in the panel).
