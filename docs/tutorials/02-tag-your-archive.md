# 2. Tag your archive

## Check first

- **Options → Maintenance → Check setup** tests LM Studio, the model and three real photos
  from your archive, and shows what the model answered. Nothing is saved.
- **Check** on the Overview page scans the archive and tells you how many photos a run
  would prepare and tag.

## Try a small batch

In **Options → Tagging options** set **Max photos** to 100 (or a **Time limit** in
minutes), save, and press **Tag new photos**. Look at the results in **Browse** before
tagging thousands.

## Tag new photos

One button runs the whole pipeline:

1. **scan** the archive — new, changed, moved and duplicated files are recognised by
   content (SHA-256), so renaming or moving folders does not re-tag anything;
2. **prepare** each photo on the CPU — EXIF, a 1024 px preview, dominant colours,
   tonality, a perceptual hash;
3. start LM Studio and load the model;
4. **tag** each photo with the vision model — the answer is structured JSON, validated
   against the taxonomy;
5. group near-duplicates, update the search index, write sidecars (if ticked), export and
   back up.

The Status card shows *Photo 312 of 2,400 · about 1 h 40 min left*; the activity log lists
each photo with its genre. Every result is saved as it lands: stop, reboot, or let a time
limit end the run — the next run continues where it stopped. Expect ~20–28 photos a minute
on a 12 GB GPU with the defaults.

## What the model records

For each photo:

- a **genre** and **subgenre** from the taxonomy (e.g. *Landscape › Mountain*), secondary
  genres, and a confidence;
- a one-sentence **caption** and a main subject;
- **keywords**;
- people (none / count / role) and animals (type, wild or domestic);
- setting, time of day, weather, season, techniques (long exposure, panorama,
  black and white…);
- a utility flag (blurry, over/underexposed, calibration frame).

PhotoMancer adds what it measures itself: the dominant **colours**, monochrome, low/high
key, and EXIF facts (long exposure, high ISO, telephoto). When the model and EXIF disagree
(e.g. "night" on a 1/1000 s daylight photo) the report lists it.

## Failures

A photo that fails is retried at the next run; after 3 attempts it is *given up*.
**Browse → Failed / untagged only** shows the reason. Tick **Retry photos that were given
up** in Options to try again.

Next: [XMP sidecars](03-xmp-immich-digikam.md).
