# 3. XMP sidecars in Immich and digiKam

PhotoMancer never modifies your JPEGs. It writes what it found into a **sidecar** next to
each photo: `IMG_1234.jpg` → `IMG_1234.jpg.xmp`, a small standard XMP file that photo
managers read.

## Writing the sidecars

- Press **Write XMP** on the Overview page (it asks first and says how many), or
- tick **Write XMP sidecars after tagging** so every run writes them.

Only tagged photos get one. A sidecar that another program wrote, or that was edited since
PhotoMancer wrote it, is **never overwritten** (`photomancer write --force` overrides).
Try `photomancer write --dry-run` to see what would be written.

## What is inside

```
lr:HierarchicalSubject   AI|Genre|Landscape|Mountain
                         AI|Genre|Landscape
                         AI|Time|Golden hour
                         AI|Technique|Long exposure
                         AI|Color|Blue
                         AI|Keyword|alps
                         AI|Review|Low confidence      (only when the model was unsure)
dc:subject               Mountain, Landscape, Golden hour, Blue, alps, …
dc:description           Snow-capped peaks above a lake at sunset.
```

Everything lives under one root tag, **AI** (configurable), so it never mixes with your
own tags and can be hidden or deleted in one click.

## Immich

Immich reads `.jpg.xmp` sidecars of **external libraries**.

1. Add your archive as an external library (Administration → External libraries).
2. If your backup folder is inside the archive, add an **exclusion pattern** with its
   folder name, e.g. `**/photomancer_backup/**`.
3. After writing the sidecars, run **Administration → Jobs → Sidecar metadata →
   Discover** (new sidecars) or **Sync** (updated ones).

The tags appear under **Tags** as a tree (`AI › Genre › Landscape › Mountain`), and the
caption as the description. Immich shows tags, not flat keywords, which is why keywords are
also written as `AI|Keyword|…` tags (`xmp.keywords_as_tags`).

## digiKam

In **Settings → Configure digiKam → Metadata → Sidecars**, tick **Read from sidecar
files** (keep the default `file.jpg.xmp` naming). Then select the album and
**Item → Reread Metadata from File**. The tags appear in the Tags tree under *AI*.

## Lightroom and others

Lightroom Classic reads XMP sidecars only for raw files; for JPEGs it reads metadata
inside the file, so it ignores these sidecars. If you need the tags inside the JPEGs,
[ExifTool](https://exiftool.org) can copy them in — note this **modifies your files**,
so try it on a copy first:

```
exiftool -tagsfromfile %d%f.%e.xmp -xmp:all -overwrite_original <folder>
```

## Removing them

**Options → Maintenance → Remove AI sidecars…** deletes every sidecar PhotoMancer wrote —
and only those. The catalogue is kept, so *Write XMP* restores them at any time.

Next: [Browse and Folders](04-browse-and-folders.md).
