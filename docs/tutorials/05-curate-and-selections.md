# 5. Curate and selections

**Curate** asks the model to pick your best photos for a theme — for a print, a portfolio
page, a slideshow, a contest.

1. Write the **theme**, as specific as you like: *misty mountains at dawn*, *portraits
   with window light*, *minimal black and white architecture*.
2. Set **Photos in the selection** (e.g. 12) and **Candidates the model looks at** (e.g.
   48 — at least as many; more candidates, better picks, longer run).
3. Press **Curate**.

What happens: search by meaning picks the candidates; the model then looks at each one
and rates how well it fits the theme and its photographic quality (composition, light,
sharpness, moment) from 0 to 10, with a one-line reason; the best (60 % fit, 40 %
quality) become the selection.

The result is a folder in `reports/curate_<date>_<theme>/` with:

- `index.html` — a contact sheet with the ratings and reasons (it opens when done);
- `selection.txt` — the list of paths, to copy the files or import into another program.

## Saved selections

The **Saved selections** list on the Curate page shows every curated selection and every
list saved from Browse (**Save list**). Double-click opens its contact sheet.

Curating takes about 5 seconds per candidate with the default model; the progress bars
show where it is.

Next: [scheduling, report and backup](06-scheduling-report-backup.md).
