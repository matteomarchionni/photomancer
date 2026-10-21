"""photomancer - index a JPEG archive with a local vision-language model.

Stages (see README.md):
    scan -> prepare (EXIF, resize, colours, pHash) -> classify (LM Studio VLM)
         -> dedup groups -> write XMP sidecars -> report

SQLite (in the data folder) is the master record; the XMP sidecars are an
export of it and can be regenerated at any time.
"""

__version__ = "2.1.0"
