# 7. Customise

## Your own taxonomy

The taxonomy is the vocabulary the model chooses from — **Options → Model and folders →
Edit taxonomy**. Make it yours:

- add or remove **genres** and **subgenres** (keep an `other_…` subgenre in each genre and
  the final `other` genre);
- write a clear **description** for each genre — the model reads it;
- add **decision rules** for pairs the model confuses ("landscape vs travel: …"); the
  report's disagreements and *Low confidence only* in Browse show where they are needed;
- bump `taxonomy_version`.

Results are stored per taxonomy: after a change, *Tag new photos* re-tags gradually, or
**Rebuild from scratch** does all of it (a backup is taken first, old results are kept).
Format: [configuration → the taxonomy](../configuration.md#the-taxonomy).

## Tagging options (Options → Tagging options)

| Option | Effect |
|---|---|
| Max photos / Time limit | cap one run (0 = no limit) — handy for evenings or a first test |
| Simultaneous requests (1–4) | photos in flight at once; 2 is a good start, benchmark before raising |
| Image size sent to the model | 1024 sees more detail; 768 is faster |
| Give the model the EXIF summary | camera, exposure and date help with night, long exposure and telephoto shots |
| Retry photos that were given up | try failed photos again |

## Another model

**Model and folders → LM Studio model**: pick an installed model (it must have **vision**)
and press **Save**. The results of the previous model are kept, not mixed.

## A custom prompt

Copy the built-in prompt idea into a `.txt` file, write `{taxonomy}` where the vocabulary
should go, and pick it under **Custom prompt file**. The answer format is enforced by a
JSON schema, so the prompt can change the *judgement*, not the fields.

## Hand-edited settings

**Edit config.yaml** opens the full file: EXIF thresholds, colour clustering, the
near-duplicate threshold, the XMP root tag, how many colours become tags — each commented.
See [configuration](../configuration.md).

## The look

**Appearance → Edit theme** opens `theme.css` — colours, fonts, font size, transparency.
Save and press **Apply**.

Next: [use it with AI agents](08-ai-agents.md).
