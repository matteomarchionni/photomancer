# 6. Scheduling, report and backup

## Weekly or monthly runs

On the Overview page choose **Weekly** or **Monthly** under *Repeat*, then press **Tag new
photos**: it runs now and registers a task with your operating system from that moment
(same weekday / day of month and time):

| System | Registered as |
|---|---|
| Windows | a Task Scheduler task "PhotoMancer Update" (only when you are logged on, never elevated) |
| macOS | a launchd agent in `~/Library/LaunchAgents` |
| Linux | a systemd user timer (or a crontab line) |

If the computer is off, the run happens as soon as it is back on (Windows, Linux).
Scheduled runs use the options saved in the panel (including *Write XMP sidecars after
tagging*), show no window, and log to `<data>/logs/run_scheduled.log`. If you open the
panel during one, it follows it live. *One-shot* runs once and leaves the schedule alone;
**Options → Maintenance → Remove schedule** removes it.

## The report

**Options → Maintenance → Report** writes an HTML report into `reports/`:

- photos per genre and subgenre; the lowest-confidence and the failed photos;
- **disagreements** between EXIF and the model (a "night" photo at 1/1000 s, a "long
  exposure" at 1/200 s) — useful to improve the taxonomy's decision rules;
- `catalog_<date>.csv` — the whole catalogue, one row per photo;
- a contact sheet with a random sample per genre, to check the tagging at a glance.

## Backup

If you set a **backup and export folder**, every run copies there:

```
catalog.db            a consistent snapshot of the catalogue
catalog.prev.db       the snapshot before (in case a bad run was backed up)
config.yaml, taxonomy.yaml, theme.css, your custom prompt
code/                 the program itself
export/               the export for AI agents (tutorial 8)
```

Previews are not copied: they are rebuilt from the photos. The folder belongs to one data
folder — a second copy of PhotoMancer pointed at it is refused, so it can never overwrite
the real backup.

If the backup folder is inside your archive, its name must be in `exclude_dirs` (the
default already has `photomancer_backup`) and in Immich's exclusion patterns.

Restoring: see [troubleshooting](../troubleshooting.md#restoring-on-a-new-pc).

Next: [customise](07-customise.md).
