# 4. Browse and Folders

## Browse

**Browse** lists the whole catalogue with a preview of the selected photo.

- Type what you are looking for and press Enter or **Search**. Search works *by meaning*:
  "foggy forest", "people at a wedding", "lonely tree in the snow" find photos whose
  caption, tags and keywords are about that, even with other words. The *Match* column
  shows how close each photo is, best first.
- The **genre** list filters by genre; **Low confidence only** shows photos the model was
  unsure about (good for checking the taxonomy); **Failed / untagged only** shows what is
  missing and why.
- Select a photo to see its preview, caption, EXIF and **exactly the tags that go into its
  sidecar**. **Open photo** and **Show in folder** do what they say; double-click opens the
  photo.
- **Save list** saves the first **Top N** photos shown as a contact sheet (HTML) plus a
  list of paths — find it later under *Curate → Saved selections*.

The search box at the top right of every page jumps to Browse.

How search works: each tagged photo is embedded (caption, main subject, genre, keywords,
setting…) with `nomic-embed-text`; your query is embedded the same way and compared, with
a bonus when a query word appears literally in the tags. The index is updated after every
run (or with **Update search index**).

Every query is embedded by LM Studio, so search by meaning needs it running. You do not
have to start it: opening Browse (or searching in Folders) starts LM Studio's server and
loads the small embedding model in the background — a few seconds on a fresh start, while
the first search shows exact-word matches and then switches to meaning by itself.

## Folders

**Folders** tells you which folders are about a topic.

1. Type a topic and choose the level: each photo's own folder, **Year (level 1)** or
   **Album (level 2)** — the first and second level below your archive root.
2. Each folder gets a **Score** (its best photos plus the folder as a whole) and
   **Matching** (hits / photos in the folder). Click a column title to sort.
3. Select a folder to see its best matching thumbnails and its most common genres and
   keywords. **Open folder** opens it; **Show in Browse** lists its matching photos.

Next: [curate and selections](05-curate-and-selections.md).
