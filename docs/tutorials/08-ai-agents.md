# 8. Use it with AI agents

After every run (or **Options → Maintenance → Export for agents**) PhotoMancer exports the
catalogue to `<backup folder>/export/` (or `<data>/export/` without a backup folder):

| File | For |
|---|---|
| `photo_catalog_index.md` | one line per photo — path, genre / subgenre, caption, keywords, date — with a self-describing header |
| `photo_catalog.jsonl` | one JSON record per photo, every field |
| `photo_catalog.sqlite` | table `photos` plus `photos_fts` (full-text), for agents that run code |
| `README_FOR_AGENTS.md` | what each file is and how to ask about it |

## Examples

Give `photo_catalog_index.md` to Claude, ChatGPT or a local model:

> Using `photo_catalog_index.md`, find my foggy mountain shots from 2019 and list their
> paths.

> From `photo_catalog_index.md`, suggest 10 photos for a calendar, one per season at
> least, and explain the choice.

Agents that run code can query the SQLite file:

```sql
SELECT path, caption FROM photos
WHERE rowid IN (SELECT rowid FROM photos_fts WHERE photos_fts MATCH 'fog AND forest')
ORDER BY date;
```

The export contains paths, captions and tags — not the photos themselves. With a synced
backup folder it is always current wherever your agents run.
