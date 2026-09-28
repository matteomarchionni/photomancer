"""
gui.py - Control panel of PhotoMancer.

Launch with the "PhotoMancer" shortcut, PhotoMancer.cmd / PhotoMancer.pyw,
or `python -m photomancer`. The frame (sidebar, Overview, job runner, progress
bars, schedule, theme) is the one shared with BiblioMancer
(_shared/app.py); this file adds the photo pages:

  Overview   Tag new photos / Check / Write XMP / Stop, repeat schedule,
             live progress, activity log
  Browse     search the catalog by meaning, preview a photo with its tags,
             save the results as a selection (contact sheet + list)
  Folders    rank folders by how well their photos match a search
  Curate     the model picks the best photos for a theme
  Options    tagging options, model and folders, maintenance, appearance
"""

from __future__ import annotations

import os
import re
import threading
import tkinter as tk
from collections import Counter
from tkinter import filedialog, messagebox

os.environ.setdefault("NUMEXPR_MAX_THREADS", str(os.cpu_count() or 8))

from . import config  # noqa: E402
from ._shared import paths, shortcuts, theme  # noqa: E402
from ._shared.app import AppSpec, DashboardApp, run_app  # noqa: E402
from ._shared.progress import Progress  # noqa: E402
from ._shared.theme import T  # noqa: E402

# --------------------------------------------------------------- palette --
# One hue - the logo's teal (~174 deg) - on near-black neutrals tinted with it.
# Amber and red are kept only for warnings and errors.
PALETTE = {
    "BG": "#0b1113", "SIDEBAR": "#0e171a", "SURFACE": "#132024", "SURFACE2": "#19292e",
    "BORDER": "#21363b", "NAV_SEL": "#1a3035",
    "TEXT": "#e4f1f0", "MUTED": "#8fb0ad", "FAINT": "#a9c5c2",
    "ACCENT": "#25968c", "ACCENT_HOVER": "#2eaba0", "ACCENT_HI": "#5cc8bc", "LINE": "#9fe3da",
    "ACCENT_LO": "#1b4541",
    "GRAD_A": "#1d7a73", "GRAD_B": "#56c2b6",
    "BAR_A": "#56c2b6", "BAR_B": "#1d7a73",
    "GREEN": "#5fd3c3", "AMBER": "#e2b36a", "RED": "#e5838b",
    "ON_ACCENT": "#ffffff", "DANGER_HOVER": "#33232a", "ROW_ALT": "#16252a",
    "TILE_TEXT": "#ffffff", "TILE_TITLE": "#062a27", "TILE_SUB": "#dff6f3", "BAR_TEXT": "#ffffff",
}
THEME_PATH = os.path.join(config.CONFIG_DIR, "theme.css")

SPEC = AppSpec(
    pkg="photomancer", pkg_file=config.PKG_FILE, title="PhotoMancer", brand="PhotoMancer",
    app_id="PhotoMancer.App", task_name="PhotoMancer Update", state_name="PhotoMancer",
    icon="photomancer_icon", palette=PALETTE, launcher="PhotoMancer.pyw",
    placeholder="Search captions, tags, folders…", size=(1360, 900), min_size=(1100, 700),
    schedule_description="PhotoMancer: scan the archive, tag new photos with the local model, "
                         "write XMP sidecars. Managed by PhotoMancer control panel.",
    idle_hint="Press Tag new photos to catalogue what is new.")

LM_STEPS = [("start LM Studio server", "Starting LM Studio", 1),
            ("unload any loaded model", "Unloading old models", 1),
            ("load ", "Loading the model", 4),
            ("model already loaded", "Model ready", 1)]


# ============================================================ progress model ===
class PhotoProgress(Progress):
    """photomancer's printed lines -> progress ('=== label ===' steps, '[i/n] OK ...' items)."""
    PLANS = {"run": [("scan photo folders", "Scanning the photo folders", 2),
                     ("prepare photos", "Preparing photos", 0)] + LM_STEPS +
                    [("tag photos", "Tagging photos", 0),
                     ("group near-duplicates", "Grouping near-duplicates", 1),
                     ("update search index", "Updating semantic search", 2),
                     ("write XMP sidecars", "Writing XMP sidecars", 3),
                     ("report", "Writing the report", 2),
                     ("export for agents", "Exporting for AI agents", 1),
                     ("back up to", "Backing up to the synced folder", 2)],
             "curate": [("find candidates", "Finding the candidates", 1)] + LM_STEPS +
                       [("rate photos", "Rating the candidates", 0),
                        ("write selection", "Writing the selection", 1)]}
    PLANS["rebuild"] = [("back up, then set", "Backing up and setting old results aside", 2)] + PLANS["run"]
    # one photo weighs about as much as three quick steps
    COUNT_STEPS = {"tag photos": ("Photo", 3, 6.0), "prepare photos": ("Photo", 1, 0.5),
                   "write XMP sidecars": ("Sidecar", 1, 0.05), "rate photos": ("Photo", 3, 5.0)}
    RE_ITEM = re.compile(r"\[(\d+)/(\d+)\]\s+(OK|FAIL|SKIP)\b\s*([\d.]+s)?\s*(.*)")
    RE_PREP = re.compile(r"prepared (\d+)/(\d+)")
    RE_SIDE = re.compile(r"sidecars (\d+)/(\d+)")
    RE_TODO = re.compile(r"(\d+) photo\(s\) to prepare, (\d+) prepared photo\(s\) waiting")
    RE_XMPN = re.compile(r"(\d+) tagged photo\(s\) to export")

    def __init__(self, kind, hint=0, label=""):
        super().__init__(kind, hint, label)
        self.tagged = 0

    def parse(self, s):
        m = self.RE_TODO.search(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            self.set_total("prepare photos", a)
            self.set_total("tag photos", a + b)
            return
        m = self.RE_XMPN.search(s)
        if m:
            self.set_total("write XMP sidecars", int(m.group(1)))
            return
        m = self.RE_ITEM.search(s)
        if m:
            key = "rate photos" if self.current_key() == "rate photos" else "tag photos"
            self.set_total(key, int(m.group(2)))
            self.set_done(key, int(m.group(1)))
            self.tagged += m.group(3) == "OK"
            rest = m.group(5)
            path = rest.split(" | ")[-1] if " | " in rest else rest
            head = rest.split(" | ")[0].split(" tok=")[0] if " | " in rest else ""
            self.item = f"{os.path.basename(path)}  —  {m.group(3)} {head}".strip()
            return
        m = self.RE_PREP.search(s)
        if m:
            self.set_done("prepare photos", int(m.group(1)))
            return
        m = self.RE_SIDE.search(s)
        if m:
            self.set_done("write XMP sidecars", int(m.group(1)))

    def item_text(self):
        return self.item[:110] if self.item else ""

    def summary(self):
        verb = "rated" if self.kind == "curate" else "tagged"
        return f"  ·  {self.tagged} photo(s) {verb}" if self.tagged else ""


SPEC.progress_cls = PhotoProgress


# ==================================================================== app ===
class App(DashboardApp):
    PAGES = [("overview", "home", "Overview"), ("browse", "browse", "Browse"),
             ("folders", "folder", "Folders"), ("curate", "star", "Curate"),
             ("options", "settings", "Options")]
    FOLDER_LINKS = [("photo", "Photo archive", "photo_root"), ("report", "Reports", "report_dir"),
                    ("cloud", "Backup / export", "backup_dir"), ("log", "Logs", "log_dir"),
                    ("folder", "Data folder", "data_dir")]
    TILES = [("files", "JPEGs in archive", "photo"), ("tagged", "Tagged", "tag"),
             ("todo", "To do", "clock"), ("fail", "Given up", "warn")]

    def __init__(self, root, spec=SPEC):
        super().__init__(root, spec)

    # ---------------------------------------------------------- state --
    def setup_state(self):
        s = self.s
        self._load_cfg()
        c = self.cfg
        self.limit = tk.StringVar(value=str(s.get("limit", 0)))
        self.minutes = tk.StringVar(value=str(s.get("minutes", 0)))
        self.retry = tk.BooleanVar(value=s.get("retry", False))
        self.write_after = tk.BooleanVar(value=s.get("write_after", False))
        self.concurrency = tk.StringVar(value=str(c.vlm["concurrency"]))
        self.image_side = tk.StringVar(value=str(c.vlm["image_side"]))
        self.exif_ctx = tk.BooleanVar(value=bool(c.vlm["exif_context"]))
        self.photo_root = tk.StringVar(value=c.photo_root)
        self.data_dir = tk.StringVar(value=c.data_dir)
        self.backup_dir = tk.StringVar(value=c.backup_dir)
        self.prompt_path = tk.StringVar(value=c.raw.get("prompt_file") or "")
        self.query = tk.StringVar()
        self.genre_f = tk.StringVar(value="All genres")
        self.only_low = tk.BooleanVar(value=False)
        self.only_fail = tk.BooleanVar(value=False)
        self.fquery = tk.StringVar()
        self.flevel = tk.StringVar(value="Photo folder")
        self.save_n = tk.StringVar(value=str(s.get("save_n", 50)))
        self.ctheme = tk.StringVar(value=s.get("curate_theme", ""))
        self.ctop = tk.StringVar(value=str(s.get("curate_top", 12)))
        self.cpool = tk.StringVar(value=str(s.get("curate_pool", 48)))
        self.rows: list[dict] = []
        self.row_for_iid: dict[str, dict] = {}
        self._preview_img = None
        self.sidx = None                    # semantic index (photomancer.search.SearchIndex)
        self.paths_by_sha: dict[str, list[str]] = {}
        self.row_by_path: dict[str, dict] = {}
        self.folder_for_iid: dict[str, dict] = {}
        self.folder_res: list[dict] = []
        self._fthumbs: list = []
        self.sel_for_iid: dict[str, str] = {}
        self.files_count = 0

    def _load_cfg(self):
        self.cfg = config.load_settings()

    def _set_subtitle(self):
        self.subtitle.config(text=f"Local VLM tagging for Immich  ·  {self.cfg.photo_root or 'no photo folder yet'}")
        self.model_lbl.config(text=f"Model  {self.cfg.lm['model']}")

    def _build(self):
        super()._build()
        self._set_subtitle()

    def persist_values(self):
        return {"limit": self.limit.get(), "minutes": self.minutes.get(), "retry": self.retry.get(),
                "write_after": self.write_after.get(), "save_n": self.save_n.get(),
                "curate_theme": self.ctheme.get(), "curate_top": self.ctop.get(),
                "curate_pool": self.cpool.get()}

    def folder_path(self, key):
        return getattr(self.cfg, key, "")

    def log_dir(self):
        return self.cfg.log_dir

    def lm_base_url(self):
        return self.cfg.lm["base_url"]

    def embedding_target(self):
        return self.cfg.lm["lms_path"], self.cfg.lm["embedding_model"]

    def _embed_args(self):
        lms, model = self.embedding_target()
        return lms, self.lm_base_url(), model

    def job_env(self):
        env = super().job_env()
        env.pop("PHOTOMANCER_ROOT", None)
        env.pop("PHOTOMANCER_DATA", None)
        return env

    def busy_buttons(self):
        return [self.btn_run, self.btn_plan, self.btn_write, self.btn_report, self.btn_prepare,
                self.btn_index, self.btn_unsched, self.btn_unwrite, self.btn_check, self.btn_export,
                self.btn_rebuild, self.btn_curate]

    def quick_search(self, q):
        self.query.set(q)
        self.show_page("browse")
        self.apply_filter()

    def on_page_shown(self, key):
        if key in ("browse", "folders", "curate") and not self.rows:
            self.load_catalog()
        if key == "curate":
            self.refresh_selections()

    # ---------------------------------------------------------- status --
    def collect_status(self):
        from .classify import active_config
        from .db import Catalog
        from .pipeline import status_counts
        from .taxonomy import load_taxonomy
        c, out = self.cfg, {}
        if os.path.exists(c.db_path):
            cfg = active_config(c, load_taxonomy(c.taxonomy_path))
            cat = Catalog(c.db_path)
            out.update(status_counts(cat, cfg["key"], int(c.vlm["max_attempts"])))
            cat.close()
        return out

    def show_counts(self, o):
        files, tagged = o.get("files"), o.get("tagged")
        if files is None:
            for k in self.tiles:
                self.tiles[k].set("–", "press Check or Tag new photos" if k == "files" else "")
            return
        self.files_count = files
        self.tiles["files"].set(f"{files:,}", "found at the last scan")
        self.tiles["tagged"].set(f"{tagged:,}", f"{tagged / files * 100:.0f}% with the current "
                                                f"settings" if files else "")
        todo = max(0, files - tagged - o.get("given_up", 0))
        self.pending_hint = todo
        self.tiles["todo"].set(f"{todo:,}", "tagged at the next run" if todo else "all caught up")
        fail = o.get("given_up", 0)
        self.tiles["fail"].set(str(fail), "tick Retry in Options" if fail else "nothing stuck")

    # --------------------------------------------------------- run card --
    def run_card(self, run):
        self.run_buttons(run, ("Tag new photos", self.do_run, "btn_run"),
                         [("Check", self.do_plan, "btn_plan"), ("Write XMP", self.do_write, "btn_write")])
        wrow = tk.Frame(run, bg=T.SURFACE)
        wrow.pack(fill="x", pady=(self.S(10), 0))
        self.check(wrow, "Write XMP sidecars after tagging", self.write_after).pack(side="left")

    # ------------------------------------------------------------ browse --
    def _page_browse(self, host):
        page = tk.Frame(host, bg=T.BG)
        page.columnconfigure(0, weight=7, uniform="br")
        page.columnconfigure(1, weight=4, uniform="br")
        page.rowconfigure(0, weight=1)
        lst = self.card(page, "Catalog", "browse")
        lst.outer.grid(row=0, column=0, sticky="nsew", padx=(0, self.S(14)))
        bar = tk.Frame(lst, bg=T.SURFACE)
        bar.pack(fill="x")
        e = self.entry(bar, self.query, size=11)
        e.pack(side="left", fill="x", expand=True, ipady=self.S(6))
        e.bind("<Return>", lambda ev: self.apply_filter())
        self.genre_box = self.combo(bar, self.genre_f, ["All genres"], width=22)
        self.genre_box.pack(side="left", padx=(self.S(8), 0), ipady=self.S(3))
        self.genre_box.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())
        self.button(bar, "Search", self.apply_filter, "primary").pack(side="left", padx=(self.S(8), 0))
        opts = tk.Frame(lst, bg=T.SURFACE)
        opts.pack(fill="x", pady=(self.S(10), self.S(10)))
        self.check(opts, "Low confidence only", self.only_low, self.apply_filter).pack(side="left")
        self.check(opts, "Failed / untagged only", self.only_fail, self.apply_filter).pack(
            side="left", padx=(self.S(16), 0))
        self.button(opts, "Reload", self.load_catalog).pack(side="right")
        self.button(opts, "Save list", self.save_list).pack(side="right", padx=(0, self.S(8)))
        self.entry(opts, self.save_n, width=5).pack(side="right", padx=(self.S(6), self.S(8)),
                                                    ipady=self.S(4))
        self.label(opts, "Top N", muted=True, size=9).pack(side="right")
        self.tree = self.make_tree(lst, [("match", 60, "Match"), ("genre", 140, "Genre"),
                                         ("sub", 150, "Subgenre"), ("conf", 55, "Conf."),
                                         ("path", 500, "File")],
                                   on_select=self.on_select, on_double=self.open_selected)
        self.browse_note = self.hint(lst, 'Search by meaning, e.g. "cat", "foggy forest", '
                                          '"people at a wedding". Double-click opens the photo.')
        self.browse_note.pack(anchor="w", fill="x", pady=(self.S(8), 0))

        pv = self.card(page, "Preview", "photo")
        pv.outer.grid(row=0, column=1, sticky="nsew")
        self.pv_img = tk.Label(pv, bg=T.SURFACE)
        self.pv_img.pack(fill="x")
        self.pv_title = self.label(pv, "", bold=True, size=11)
        self.pv_title.pack(fill="x", pady=(self.S(8), 0))
        self.pv_caption = self.label(pv, "Select a photo.", muted=True, size=9)
        self.pv_caption.pack(fill="x", pady=(self.S(4), 0))
        self.pv_swatch = tk.Frame(pv, bg=T.SURFACE, height=self.S(14))
        self.pv_swatch.pack(fill="x", pady=(self.S(8), 0))
        self.pv_tags = self.readonly_text(pv, height=10)
        self.pv_tags.pack(fill="both", expand=True, pady=(self.S(8), 0))
        prow = tk.Frame(pv, bg=T.SURFACE)
        prow.pack(fill="x", pady=(self.S(8), 0))
        self.button(prow, "Open photo", self.open_selected).pack(side="left")
        self.button(prow, "Show in folder", self.reveal_selected).pack(side="left", padx=(self.S(8), 0))
        pv.bind("<Configure>", lambda e: [w.config(wraplength=max(100, e.width - self.S(10)))
                                          for w in (self.pv_title, self.pv_caption)])
        return page

    def load_catalog(self):
        self.browse_note.config(text="Loading the catalog…", fg=T.MUTED)
        self.ensure_semantic()              # search by meaning needs LM Studio running

        def work():
            rows, err = [], ""
            try:
                from .classify import active_config
                from .db import Catalog
                from .report import catalog_rows
                from .taxonomy import load_taxonomy
                c = self.cfg
                if os.path.exists(c.db_path):
                    cfg = active_config(c, load_taxonomy(c.taxonomy_path))
                    cat = Catalog(c.db_path)
                    rows = catalog_rows(c, cat, cfg["key"])
                    self._load_index(c, cat, cfg["key"], sum(1 for r in rows if r["result"]))
                    cat.close()
                else:
                    err = "No catalog yet - press Tag new photos first."
            except Exception as exc:
                err = f"Could not load the catalog: {exc}"
            self.q.put(lambda: self._catalog_loaded(rows, err))
        threading.Thread(target=work, daemon=True).start()

    def _load_index(self, c, cat, key, n_tagged):
        """(Worker thread) load the semantic index; top it up if photos are missing."""
        from ._shared.lmstudio import ensure_embedding
        from .search import SearchIndex, update_index
        idx = None
        try:
            idx = SearchIndex(c, cat, key)
            if len(idx) < n_tagged:
                ensure_embedding(c.lm["lms_path"], c.lm["base_url"], c.lm["embedding_model"])
                missing = n_tagged - len(idx)
                self.q.put(lambda: self.browse_note.config(
                    text=f"Indexing {missing} photo(s) for semantic search…", fg=T.MUTED))
                update_index(c, cat, key)
                idx = SearchIndex(c, cat, key)
        except Exception as exc:
            msg = f"search index: {type(exc).__name__}: {exc}\n"
            self.q.put(lambda: self.log(msg, "warn"))
        self.sidx = idx if idx is not None and len(idx) else None

    def _semantic(self, query):
        """((sha -> score, hit shas), '') or (None, reason) when unavailable."""
        if self.sidx is None:
            return None, "semantic index not available"
        try:
            import numpy as np
            from .search import relevant
            sc = self.sidx.scores(query)
            hit = relevant(sc)
            return ({s: float(sc[i]) for i, s in enumerate(self.sidx.shas)},
                    {self.sidx.shas[i] for i in np.nonzero(hit)[0]}), ""
        except Exception as exc:
            return None, self.semantic_retry(query, exc)

    def _catalog_loaded(self, rows, err):
        from .search import words_of
        self.rows = rows
        self.paths_by_sha, self.row_by_path = {}, {}
        for r in rows:
            self.row_by_path[r["path"]] = r
            if r["sha256"]:
                self.paths_by_sha.setdefault(r["sha256"], []).append(r["path"])
            res = r["result"] or {}
            r["_text"] = " ".join([r["path"], res.get("caption", ""), res.get("main_subject", ""),
                                   " ".join(res.get("keywords") or []),
                                   " ".join(res.get("subject_tags") or []),
                                   res.get("primary_genre", ""), res.get("subgenre", "")]).lower()
            r["_words"] = words_of(r["_text"].replace("_", " "))
        genres = sorted({r["result"]["primary_genre"] for r in rows if r["result"]})
        self.genre_box["values"] = ["All genres"] + genres
        if err:
            self.browse_note.config(text=err, fg=T.AMBER)
        self.apply_filter()
        if self.fquery.get().strip():
            self.search_folders()

    def apply_filter(self):
        from .search import words_of
        q = self.query.get().strip().lower()
        if q == self.spec.placeholder.lower():
            q = ""
        g = self.genre_f.get()
        low = float(self.cfg.report["low_confidence"])
        sem, why = self._semantic(q) if q else (None, "")
        scores, hits = sem if sem else ({}, set())
        qw = words_of(q)
        out = []
        for r in self.rows:
            res = r["result"]
            if self.only_fail.get() and res:
                continue
            if g != "All genres" and (not res or res["primary_genre"] != g):
                continue
            if self.only_low.get() and (not res or res["genre_confidence"] >= low):
                continue
            if q and sem is not None and r["sha256"] not in hits:
                continue
            if q and sem is None and not qw <= r["_words"]:
                continue                    # fallback: whole words, never substrings
            out.append(r)
        if q and sem is not None:
            out.sort(key=lambda r: -scores.get(r["sha256"], 0.0))
        self.shown = out
        self.tree.delete(*self.tree.get_children())
        self.row_for_iid.clear()
        for i, r in enumerate(out[:5000]):
            res = r["result"] or {}
            tags = ["odd"] if i % 2 else []
            if not res:
                tags.append("bad")
            iid = self.tree.insert("", "end", values=(
                f"{scores[r['sha256']]:.2f}" if q and r["sha256"] in scores else "",
                res.get("primary_genre", "failed" if r["error"] else "not tagged"),
                res.get("subgenre", ""),
                f"{res['genre_confidence']:.2f}" if res else "", r["path"]), tags=tags)
            self.row_for_iid[iid] = r
        more = " (showing 5000)" if len(out) > 5000 else ""
        if self.rows:
            how = ("" if not q else "  ·  semantic search, best match first" if sem is not None
                   else f"  ·  exact-word search ({why})")
            self.browse_note.config(text=f"{len(out)} of {len(self.rows)} photos{more}{how}. "
                                         "Double-click opens the photo.",
                                    fg=T.AMBER if q and sem is None else T.FAINT)

    def save_list(self):
        """Save the first N photos shown in Browse as a contact sheet + list of paths."""
        rows = [r for r in getattr(self, "shown", []) if r["result"]]
        if not rows:
            messagebox.showinfo("Save list", "Search first: the list is what Browse shows.")
            return
        try:
            n = max(1, min(500, int(self.save_n.get())))
        except ValueError:
            messagebox.showerror("Save list", "Top N must be a whole number.")
            return
        from .curate import write_selection
        q = self.query.get().strip()
        title = f"Selection: {q}" if q and q != self.spec.placeholder else "Selection"
        path = write_selection(self.cfg, rows[:n], title)
        self.persist()
        self.browse_note.config(text=f"Saved {min(n, len(rows))} photo(s): {path}", fg=T.GREEN)
        self.log(f"\nSelection saved: {path}\n", "ok")
        paths.open_path(path)

    def _selected(self):
        sel = self.tree.selection()
        return self.row_for_iid.get(sel[0]) if sel else None

    def on_select(self, _e=None):
        r = self._selected()
        if not r:
            return
        res = r["result"]
        try:
            from PIL import Image, ImageTk
            p = self.cfg.cache_path(r["sha256"]) if r["sha256"] else ""
            if p and os.path.exists(p):
                im = Image.open(p)
                w = max(200, self.pv_img.winfo_width() or self.S(380))
                im.thumbnail((w, int(w * 0.8)))
                self._preview_img = ImageTk.PhotoImage(im)
                self.pv_img.config(image=self._preview_img)
            else:
                self.pv_img.config(image="")
        except Exception:
            self.pv_img.config(image="")
        for wdg in self.pv_swatch.winfo_children():
            wdg.destroy()
        x = 0.0
        for _name, frac, hexv in r["colors"]:           # colour bar, widths = pixel fractions
            tk.Frame(self.pv_swatch, bg=hexv).place(relx=x, rely=0, relwidth=frac, relheight=1)
            x += frac
        if res:
            self.pv_title.config(text=f"{res['primary_genre']} / {res['subgenre']}   "
                                      f"{res['genre_confidence']:.2f}")
            self.pv_caption.config(text=res["caption"], fg=T.TEXT)
            from .report import row_tags
            e = r["exif"]
            ex = ", ".join(x for x in (e.get("camera"), e.get("lens"),
                                       f"{e['focal_35mm']:.0f} mm" if e.get("focal_35mm") else "",
                                       f"f/{e['aperture']:.1f}" if e.get("aperture") else "",
                                       f"{e['shutter_s']:.3g} s" if e.get("shutter_s") else "",
                                       f"ISO {e['iso']}" if e.get("iso") else "",
                                       e.get("datetime")) if x)
            self.set_text(self.pv_tags, "\n".join(row_tags(self.cfg, r))
                          + (f"\n\n{ex}" if ex else "") + f"\n\n{r['path']}")
        else:
            self.pv_title.config(text="not tagged")
            self.pv_caption.config(text=r["error"] or "Waiting for the next run.", fg=T.AMBER)
            self.set_text(self.pv_tags, r["path"])

    def open_selected(self):
        r = self._selected()
        if r:
            self.open_path(os.path.join(self.cfg.photo_root, r["path"]))

    def reveal_selected(self):
        r = self._selected()
        if r:
            paths.reveal(os.path.join(self.cfg.photo_root, r["path"]))

    # ----------------------------------------------------------- folders --
    def _page_folders(self, host):
        page = tk.Frame(host, bg=T.BG)
        page.columnconfigure(0, weight=7, uniform="fo")
        page.columnconfigure(1, weight=4, uniform="fo")
        page.rowconfigure(0, weight=1)
        lst = self.card(page, "Folders by content", "folder")
        lst.outer.grid(row=0, column=0, sticky="nsew", padx=(0, self.S(14)))
        bar = tk.Frame(lst, bg=T.SURFACE)
        bar.pack(fill="x")
        e = self.entry(bar, self.fquery, size=11)
        e.pack(side="left", fill="x", expand=True, ipady=self.S(6))
        e.bind("<Return>", lambda ev: self.search_folders())
        lv = self.combo(bar, self.flevel, ["Photo folder", "Year (level 1)", "Album (level 2)"], width=16)
        lv.pack(side="left", padx=(self.S(8), 0), ipady=self.S(3))
        lv.bind("<<ComboboxSelected>>", lambda e: self.search_folders())
        self.button(bar, "Search", self.search_folders, "primary").pack(side="left", padx=(self.S(8), 0))
        self.hint(lst, "Ranks folders by how well the tags of the photos inside match the search "
                       "(by meaning). Score = its best photos + the folder as a whole. Matching = "
                       "photos that are a hit / photos in the folder.").pack(
            anchor="w", fill="x", pady=(self.S(8), self.S(8)))
        self.ftree = self.make_tree(lst, [("score", 70, "Score"), ("hits", 110, "Matching"),
                                          ("folder", 600, "Folder")],
                                    on_select=self.on_folder_select, on_double=self.open_folder,
                                    sort=self._sort_folders)
        self.folder_note = self.hint(lst, f"Double-click opens the folder in {paths.FILE_MANAGER}.")
        self.folder_note.pack(anchor="w", fill="x", pady=(self.S(8), 0))

        pv = self.card(page, "Best matches in the folder", "photo")
        pv.outer.grid(row=0, column=1, sticky="nsew")
        self.fgrid = tk.Frame(pv, bg=T.SURFACE)
        self.fgrid.pack(fill="x")
        self.ftitle = self.label(pv, "", bold=True, size=10)
        self.ftitle.pack(fill="x", pady=(self.S(8), 0))
        self.fsummary = self.readonly_text(pv, height=10)
        self.fsummary.pack(fill="both", expand=True, pady=(self.S(6), 0))
        frow = tk.Frame(pv, bg=T.SURFACE)
        frow.pack(fill="x", pady=(self.S(8), 0))
        self.button(frow, "Open folder", self.open_folder).pack(side="left")
        self.button(frow, "Show in Browse", self.folder_to_browse).pack(side="left", padx=(self.S(8), 0))
        pv.bind("<Configure>", lambda e: self.ftitle.config(wraplength=max(100, e.width - 10)))
        return page

    def search_folders(self):
        q = self.fquery.get().strip()
        if not q:
            return
        if self.sidx is None:
            self.folder_note.config(text="Semantic index not available yet (loading, or LM Studio "
                                         "is off) - try again in a moment.", fg=T.AMBER)
            if not self.rows:
                self.load_catalog()
            return
        level = {"Photo folder": 0, "Year (level 1)": 1, "Album (level 2)": 2}[self.flevel.get()]
        self.folder_note.config(text="Searching…", fg=T.MUTED)
        idx, paths_by = self.sidx, self.paths_by_sha

        def work():
            try:
                from ._shared.lmstudio import ensure_embedding
                from .search import search_folders
                ensure_embedding(*self._embed_args())
                res, err = search_folders(idx, paths_by, q, level), ""
            except Exception as exc:
                res, err = [], f"Search failed: {type(exc).__name__}: {exc}"
            self.q.put(lambda: self._folders_found(res, err))
        threading.Thread(target=work, daemon=True).start()

    def _folders_found(self, res, err):
        self.folder_res = res
        self._fill_folders(res)
        self.folder_note.config(text=err or f"{len(res)} folders ranked. Click a column title to "
                                            "sort; double-click opens the folder.",
                                fg=T.AMBER if err else T.FAINT)

    def _fill_folders(self, res):
        self.ftree.delete(*self.ftree.get_children())
        self.folder_for_iid.clear()
        for i, d in enumerate(res[:2000]):
            iid = self.ftree.insert("", "end", values=(f"{d['score']:.3f}", f"{d['hits']} / {d['photos']}",
                                                       d["folder"]), tags=("odd",) if i % 2 else ())
            self.folder_for_iid[iid] = d
        kids = self.ftree.get_children()
        if kids:
            self.ftree.selection_set(kids[0])

    def _sort_folders(self, col):
        key = {"score": lambda d: -d["score"], "hits": lambda d: (-d["hits"], -d["score"]),
               "folder": lambda d: d["folder"].lower()}[col]
        self._fill_folders(sorted(self.folder_res, key=key))

    def _sel_folder(self):
        sel = self.ftree.selection()
        return self.folder_for_iid.get(sel[0]) if sel else None

    def on_folder_select(self, _e=None):
        d = self._sel_folder()
        if not d:
            return
        from PIL import Image, ImageTk
        for w in self.fgrid.winfo_children():
            w.destroy()
        self._fthumbs = []
        cell = max(90, (self.fgrid.winfo_width() or self.S(380)) // 3 - self.S(6))
        for k, sha in enumerate(d["best"][:6]):
            try:
                im = Image.open(self.cfg.cache_path(sha, "thumb"))
                im.thumbnail((cell, cell))
                ph = ImageTk.PhotoImage(im)
            except Exception:
                continue
            self._fthumbs.append(ph)
            lb = tk.Label(self.fgrid, image=ph, bg=T.SURFACE, cursor="hand2")
            lb.grid(row=k // 3, column=k % 3, padx=self.S(3), pady=self.S(3))
            path = (self.paths_by_sha.get(sha) or [""])[0]
            lb.bind("<Double-1>", lambda e, p=path: self.open_path(os.path.join(self.cfg.photo_root, p)))
        self.ftitle.config(text=d["folder"])
        prefix = "" if d["folder"] == "(archive root)" else d["folder"] + os.sep
        members = [r for p, r in self.row_by_path.items()
                   if r["result"] and (p.startswith(prefix) if prefix else os.sep not in p)]
        genres = Counter(f"{r['result']['primary_genre']} / {r['result']['subgenre']}" for r in members)
        kws = Counter(k for r in members for k in (r["result"].get("keywords") or []))
        txt = [f"{d['hits']} of {d['photos']} photos match  ·  score {d['score']:.3f}", "",
               "Most common in this folder:"]
        txt += [f"  {g}  ({n})" for g, n in genres.most_common(5)]
        txt += ["", "Keywords: " + ", ".join(k for k, _n in kws.most_common(15)),
                "", "Double-click a thumbnail to open the photo."]
        self.set_text(self.fsummary, "\n".join(txt))

    def open_folder(self):
        d = self._sel_folder()
        if d:
            self.open_path(self.cfg.photo_root if d["folder"] == "(archive root)"
                           else os.path.join(self.cfg.photo_root, d["folder"]))

    def folder_to_browse(self):
        """Open Browse with the same search, limited to the selected folder."""
        d = self._sel_folder()
        if not d:
            return
        self.query.set(self.fquery.get())
        self.show_page("browse")
        self.apply_filter()
        prefix = "" if d["folder"] == "(archive root)" else d["folder"] + os.sep
        for iid, r in list(self.row_for_iid.items()):
            if not (r["path"].startswith(prefix) if prefix else os.sep not in r["path"]):
                self.tree.delete(iid)
                self.row_for_iid.pop(iid)
        self.browse_note.config(text=f"{len(self.row_for_iid)} matching photo(s) in "
                                     f"{d['folder']}, best match first.", fg=T.FAINT)

    # ------------------------------------------------------------ curate --
    def _page_curate(self, host):
        page = tk.Frame(host, bg=T.BG)
        c = self.card(page, "Curate a selection", "star", fill="x")
        self.label(c, "Theme - what the selection is about (e.g. \"misty mountains at dawn\", "
                      "\"portraits in window light\", \"our 2023 trip to Norway, best of\")",
                   muted=True, size=9).pack(anchor="w", pady=(0, self.S(4)))
        bar = tk.Frame(c, bg=T.SURFACE)
        bar.pack(fill="x")
        e = self.entry(bar, self.ctheme, size=11)
        e.pack(side="left", fill="x", expand=True, ipady=self.S(6))
        self.btn_curate = self.button(bar, "Curate", self.do_curate, "primary")
        self.btn_curate.pack(side="left", padx=(self.S(8), 0))
        opts = tk.Frame(c, bg=T.SURFACE)
        opts.pack(fill="x", pady=(self.S(10), 0))
        for lab, var in (("Photos in the selection", self.ctop), ("Candidates the model looks at",
                                                                  self.cpool)):
            self.label(opts, lab, muted=True, size=9).pack(side="left")
            self.entry(opts, var, width=6).pack(side="left", padx=(self.S(6), self.S(20)), ipady=self.S(4))
        self.hint(c, "Search by meaning picks the candidates; the model then looks at each photo and "
                     "rates how well it fits the theme and how strong it is as a photograph. The best "
                     "ones become a contact sheet with a list of paths (opens when done). About 5 s "
                     "per candidate.").pack(anchor="w", fill="x", pady=(self.S(10), 0))

        lst = self.card(page, "Saved selections", "list", fill="both", expand=True, pady=(self.S(16), 0))
        self.seltree = self.make_tree(lst, [("when", 150, "Saved"), ("n", 70, "Photos"),
                                            ("title", 700, "Selection")], on_double=self.open_selection)
        self.hint(lst, "Curated selections and lists saved from Browse. Double-click opens the "
                       "contact sheet.").pack(anchor="w", fill="x", pady=(self.S(8), 0))
        return page

    def refresh_selections(self):
        self.seltree.delete(*self.seltree.get_children())
        self.sel_for_iid.clear()
        base = self.cfg.report_dir
        try:
            names = sorted((n for n in os.listdir(base) if n.startswith(("curate_", "selection_"))),
                           reverse=True)
        except OSError:
            names = []
        for i, n in enumerate(names):
            txt = os.path.join(base, n, "selection.txt")
            try:
                with open(txt, encoding="utf-8") as fh:
                    title, count = fh.readline().strip(), fh.readline().split(" ")[0]
            except OSError:
                title, count = n, ""
            m = re.match(r"\w+_(\d{4}-\d\d-\d\d)_(\d\d)(\d\d)", n)
            when = f"{m.group(1)} {m.group(2)}:{m.group(3)}" if m else ""
            iid = self.seltree.insert("", "end", values=(when, count, title), tags=("odd",) if i % 2 else ())
            self.sel_for_iid[iid] = os.path.join(base, n, "index.html")

    def open_selection(self):
        sel = self.seltree.selection()
        if sel:
            self.open_path(self.sel_for_iid.get(sel[0], ""))

    def do_curate(self):
        t = self.ctheme.get().strip()
        if not t:
            messagebox.showinfo("Curate", "Write a theme first.")
            return
        try:
            top, pool = int(self.ctop.get()), int(self.cpool.get())
            if not (1 <= top <= 200 and top <= pool <= 1000):
                raise ValueError
        except ValueError:
            messagebox.showerror("Curate", "Photos: 1-200; candidates: at least as many, up to 1000.")
            return
        if not self._model_ok():
            return
        self.start(["curate", t, "--top", str(top), "--pool", str(pool)], f"curate: {t}", "curate",
                   hint=pool)

    # ----------------------------------------------------------- options --
    def _page_options(self, host):
        outer, page = self.scrollable(host)
        page.columnconfigure((0, 1), weight=1, uniform="opt")

        opt = self.card(page, "Tagging options", "settings")
        opt.outer.grid(row=1, column=0, sticky="nsew", padx=(0, self.S(14)))
        grid = tk.Frame(opt, bg=T.SURFACE)
        grid.pack(fill="x")
        for i, (lab, var) in enumerate((("Max photos (0 = all)", self.limit),
                                        ("Time limit, min (0 = none)", self.minutes),
                                        ("Simultaneous requests (1-4)", self.concurrency))):
            self.label(grid, lab, muted=True, size=9).grid(row=0, column=i, sticky="w",
                                                           padx=(0 if i == 0 else self.S(12), 0))
            self.entry(grid, var, width=10).grid(row=1, column=i, sticky="we", ipady=self.S(5),
                                                 padx=(0 if i == 0 else self.S(12), 0),
                                                 pady=(self.S(4), 0))
            grid.columnconfigure(i, weight=1)
        self.label(opt, "Image size sent to the model", muted=True, size=9).pack(
            anchor="w", pady=(self.S(14), self.S(5)))
        self.segmented(opt, self.image_side, (("768", "768 px"), ("1024", "1024 px"))).pack(fill="x")
        flags = tk.Frame(opt, bg=T.SURFACE)
        flags.pack(fill="x", pady=(self.S(12), 0))
        self.check(flags, "Give the model the EXIF summary (camera, exposure, date)",
                   self.exif_ctx).pack(anchor="w", pady=self.S(2))
        self.check(flags, "Retry photos that were given up", self.retry).pack(anchor="w", pady=self.S(2))
        r = tk.Frame(opt, bg=T.SURFACE)
        r.pack(fill="x", pady=(self.S(12), 0))
        self.button(r, "Save options", self.save_options, "primary").pack(side="left")
        self.opt_msg = self.hint(r, "Image size, EXIF and requests go to config.yaml (the scheduled "
                                    "run uses them too).")
        self.opt_msg.pack(side="left", fill="x", expand=True, padx=(self.S(12), 0))

        pc = self.card(page, "Model and folders", "folder")
        pc.outer.grid(row=1, column=1, sticky="nsew")
        self.label(pc, "LM Studio model (must have vision)", muted=True, size=9).pack(
            anchor="w", pady=(0, self.S(4)))
        mrow = tk.Frame(pc, bg=T.SURFACE)
        mrow.pack(fill="x")
        self.model_var = tk.StringVar(value=self.cfg.lm["model"])
        self.model_box = self.combo(mrow, self.model_var, readonly=False)
        self.model_box.pack(side="left", fill="x", expand=True, ipady=self.S(3))
        self.model_box.bind("<Button-1>", lambda e: self.refresh_models()
                            if not self.model_box["values"] else None)
        self.button(mrow, "Save", self.save_model, "primary").pack(side="left", padx=(self.S(8), 0))
        self.model_msg = self.hint(pc, "Pick or type a model key, then Save. Changing the model "
                                       "re-queues every photo (results are per model).")
        self.model_msg.pack(fill="x", pady=(self.S(3), 0))
        self.path_row(pc, "Photo archive", self.photo_root,
                      lambda: self.pick_dir(self.photo_root, "photo archive"), "Only ever read.")
        self.path_row(pc, "Data folder (database, cache, reports)", self.data_dir,
                      lambda: self.pick_dir(self.data_dir, "data folder"),
                      "Keep it on a local disk, not on a cloud drive.")
        self.path_row(pc, "Backup and export folder (synced)", self.backup_dir,
                      lambda: self.pick_dir(self.backup_dir, "backup folder"),
                      "Catalog backup + export for AI agents after every run. Inside the archive, "
                      "use a folder listed in exclude_dirs. Empty = none.")
        self.path_row(pc, "Custom prompt file", self.prompt_path, self.pick_prompt,
                      "Empty = built-in prompt. {taxonomy} marks where the vocabulary goes.")
        r = tk.Frame(pc, bg=T.SURFACE)
        r.pack(fill="x", pady=(self.S(12), 0))
        self.button(r, "Save folders", self.save_paths, "primary").pack(side="left")
        self.button(r, "Edit taxonomy", lambda: paths.edit_file(self.cfg.taxonomy_path)).pack(
            side="left", padx=(self.S(8), 0))
        self.button(r, "Edit config.yaml", lambda: paths.edit_file(config.CONFIG_PATH)).pack(
            side="left", padx=(self.S(8), 0))

        mt = self.card(page, "Maintenance", "warn")
        mt.outer.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, self.S(16)))
        self.action_rows(mt, [
            ("Report", "Statistics, EXIF/model disagreements, CSV of the whole catalog and a "
                       "contact sheet per genre. Opens when ready.", "btn_report",
             lambda: self.start(["report"], "report", "report"), "secondary"),
            ("Check setup", "LM Studio, the model and three real test photos. Changes nothing.",
             "btn_check", lambda: self.start(["check"], "check setup", "check"), "secondary"),
            ("Prepare only", "Reads every photo once (hash, EXIF, colours, previews) without the "
                             "model - handy to do overnight before tagging.", "btn_prepare",
             self.do_prepare, "secondary"),
            ("Update search index", "Embeds tagged photos for semantic search (done automatically "
                                    "after every run; seconds per thousand).", "btn_index",
             lambda: self.start(["index"], "update search index", "index"), "secondary"),
            ("Export for agents", "Catalog as Markdown / JSONL / SQLite for Claude, ChatGPT or a "
                                  "local model (also after every run).", "btn_export",
             lambda: self.start(["export"], "export for agents", "export"), "secondary"),
            ("Remove schedule", "Stops the weekly/monthly task. Tag new photos keeps working.",
             "btn_unsched", self.do_unschedule, "secondary"),
            ("Remove AI sidecars…", "Deletes every .jpg.xmp this tool wrote (and only those). The "
                                    "catalog keeps the results; Write XMP recreates them.",
             "btn_unwrite", self.do_unwrite, "danger"),
            ("Rebuild from scratch…", "Backs up, sets the current results aside (kept in the "
                                      "catalog) and tags every photo again. Hours.", "btn_rebuild",
             self.do_rebuild, "danger")])
        self.appearance_card(page, 2)
        return outer

    # ----------------------------------------------------------- actions --
    def model_check(self):
        from .lmstudio import check_model
        return check_model(self.cfg.lm["lms_path"], self.cfg.lm["model"],
                           int(self.cfg.lm["context_per_request"]))

    def _run_opts(self) -> list[str]:
        a = []
        try:
            if int(self.limit.get() or 0) > 0:
                a += ["--limit", str(int(self.limit.get()))]
            if float(self.minutes.get() or 0) > 0:
                a += ["--max-minutes", str(float(self.minutes.get()))]
        except ValueError:
            raise ValueError("Max photos and time limit must be numbers.")
        if self.retry.get():
            a.append("--retry-failed")
        return a

    def do_run(self):
        try:
            opts = self._run_opts()
        except ValueError as exc:
            messagebox.showerror("Options", str(exc))
            return
        if not self._model_ok():
            return
        if self.write_after.get():
            opts.append("--write-xmp")
        self.register_schedule(["run", "--max-minutes", "240"] +
                               (["--write-xmp"] if self.write_after.get() else []))
        self.start(["run", *opts], "tag new photos", "run", hint=self.pending_hint)

    def do_plan(self):
        self.start(["plan"], "check what a run would do", "plan")

    def do_write(self):
        try:
            lim = int(self.limit.get() or 0)
        except ValueError:
            lim = 0
        what = (f"the first {lim} tagged JPEGs (Options > Max photos)" if lim > 0
                else "every tagged JPEG")
        if not messagebox.askyesno(
                "Write XMP sidecars",
                f"Write a .jpg.xmp sidecar next to {what} in\n{self.cfg.photo_root}\n\nThe photos "
                "themselves are never modified. Sidecars written by other programs, or edited since "
                "this tool wrote them, are left alone. Continue?"):
            return
        self.start(["write", *(["--limit", str(lim)] if lim > 0 else [])], "write XMP sidecars", "write")

    def do_prepare(self):
        try:
            opts = self._run_opts()
        except ValueError as exc:
            messagebox.showerror("Options", str(exc))
            return
        lim = ["--limit", opts[opts.index("--limit") + 1]] if "--limit" in opts else []
        self.start(["prepare", *lim], "prepare photos (no model)", "prepare")

    def do_unwrite(self):
        if not messagebox.askyesno("Remove AI sidecars",
                                   "Delete every .jpg.xmp sidecar written by this tool?\n\n"
                                   "Sidecars from other programs are not touched, and the catalog "
                                   "keeps every result.", icon="warning"):
            return
        self.start(["unwrite", "--yes"], "remove AI sidecars", "unwrite")

    def do_rebuild(self):
        if not self._model_ok():
            return
        if not messagebox.askyesno(
                "Rebuild from scratch",
                "Every photo is tagged again with the current model, prompt and taxonomy.\n\n"
                "A backup is taken first; the current results are kept in the catalog (set aside, "
                "not deleted). The archive is only read.\n\n"
                f"About {self.files_count / 1200 + 0.5:.0f} hour(s) at ~20 photos/min. Continue?",
                icon="warning"):
            return
        self.start(["rebuild", "--yes"] + (["--write-xmp"] if self.write_after.get() else []),
                   "rebuild from scratch", "rebuild")

    def refresh_models(self):
        from .lmstudio import installed_models
        models = installed_models(self.cfg.lm["lms_path"], "llm")
        if models is None:
            self.model_msg.config(text="Cannot list models: LM Studio's lms not found.", fg=T.AMBER)
            return
        self.model_box["values"] = [m["modelKey"] for m in models if m.get("vision")]

    def _save_cfg(self, changes: dict, msg_lbl, what: str):
        try:
            config.update_config(changes)
        except Exception as exc:
            messagebox.showerror("config.yaml", f"Could not save: {exc}")
            return False
        self._load_cfg()
        self._set_subtitle()
        msg_lbl.config(text=f"Saved {what} to config.yaml.", fg=T.GREEN)
        self.log(f"\nSaved {what}: {changes}\n", "ok")
        self.rows = []
        self.refresh_status()
        return True

    def save_model(self):
        from .lmstudio import check_model
        name = self.model_var.get().strip()
        if not name:
            return
        ok, msg = check_model(self.cfg.lm["lms_path"], name, int(self.cfg.lm["context_per_request"]))
        if not ok:
            self.model_msg.config(text=msg.splitlines()[0], fg=T.RED)
            messagebox.showerror("Model not available", msg)
            return
        self._save_cfg({"lmstudio.model": name}, self.model_msg, "the model")

    def save_options(self):
        try:
            c = int(self.concurrency.get())
            if not 1 <= c <= 8:
                raise ValueError
        except ValueError:
            messagebox.showerror("Options", "Simultaneous requests must be a whole number 1-8.")
            return
        self.persist()
        self._save_cfg({"vlm.concurrency": c, "vlm.image_side": int(self.image_side.get()),
                        "vlm.exif_context": bool(self.exif_ctx.get())}, self.opt_msg, "options")

    def save_paths(self):
        root, data, bk = (v.get().strip() for v in (self.photo_root, self.data_dir, self.backup_dir))
        if not os.path.isdir(root):
            messagebox.showerror("Folders", f"Photo archive not found:\n{root}")
            return
        pr = self.prompt_path.get().strip()
        if pr and not os.path.exists(pr):
            messagebox.showerror("Folders", f"Prompt file not found:\n{pr}")
            return
        if paths.looks_like_cloud_folder(data) and not messagebox.askyesno(
                "Data folder", "The data folder looks like a cloud-drive folder. SQLite there can be "
                               "locked by the sync client mid-run. Use it anyway?"):
            return
        self._save_cfg({"photo_root": root, "data_dir": data, "backup_dir": bk, "prompt_file": pr},
                       self.model_msg, "folders")

    def pick_dir(self, var, what):
        p = filedialog.askdirectory(title=f"Select the {what}", initialdir=var.get() or None)
        if p:
            var.set(os.path.normpath(p))

    def pick_prompt(self):
        p = filedialog.askopenfilename(title="Select a system prompt file",
                                       filetypes=[("Text", "*.txt *.md"), ("All files", "*.*")])
        if p:
            self.prompt_path.set(os.path.normpath(p))

    def after_job(self, rc):
        self.rows = []                      # browse/folders pages reload on next visit
        self.sidx = None
        if self.pages["curate"].winfo_ismapped():
            self.refresh_selections()


# ================================================================ helpers ===
def _init_theme():
    theme.init(PALETTE, SPEC.title, THEME_PATH)


def launcher_argv() -> list[str]:
    """Command a shortcut runs to open the panel."""
    argv = paths.launcher_command(SPEC.pkg, SPEC.pkg_file, SPEC.launcher)
    return argv if paths.FROZEN or argv[-1] != SPEC.pkg else [*argv, "gui"]


def make_shortcuts(desktop=False, menu=True, remove=False) -> int:
    if remove:
        msgs = shortcuts.remove(SPEC.title)
    else:
        assets = paths.resource(SPEC.pkg_file, "assets")
        msgs = shortcuts.create(SPEC.title, launcher_argv(), paths.app_home(SPEC.pkg_file),
                                icon_png=os.path.join(assets, "photomancer_icon_256.png"),
                                icon_ico=os.path.join(assets, "photomancer_icon.ico"),
                                desktop=desktop, menu=menu,
                                comment="Tag a photo archive with a local vision model")
    print("\n".join(msgs) or "nothing to do")
    return 0


def run_wizard() -> int:
    """First-run setup: folders, LM Studio check, shortcuts -> config.yaml."""
    from ._shared import lmstudio
    from ._shared.wizard import Field
    from ._shared.wizard import run_wizard as wizard
    _init_theme()

    def lm_check():
        lms = lmstudio.find_lms()
        if not lms:
            return [(False, "LM Studio is not installed (free, from lmstudio.ai). Install it, "
                            "open it once, download qwen/qwen3.5-9b, then press Check again.")]
        ok, msg = lmstudio.check_model(lms, config.DEFAULTS["lmstudio"]["model"], 8192, need_vision=True)
        return [(True, f"LM Studio found: {lms}"),
                (ok, msg if ok else msg.splitlines()[0] + " - download it in LM Studio (Discover).")]
    values = wizard(SPEC.title, "Choose your photo archive and where the catalog should live. "
                                "Photos are only read; tags go into .xmp sidecars that Immich "
                                "and digiKam read. Everything runs locally with LM Studio.",
                    [Field("photo_root", "Your photo archive (JPEGs)", hint="Only ever read."),
                     Field("data_dir", "Data folder (catalog, previews, reports)",
                           default=config.default_data_dir(), local_only=True,
                           hint="On a local disk; about 1 GB per 5,000 photos for the previews."),
                     Field("backup_dir", "Synced backup / export folder", required=False,
                           hint="E.g. a Google Drive / OneDrive folder: catalog backup and an "
                                "export for AI agents after every run.")],
                    lm_check, icon_png=paths.resource(SPEC.pkg_file, "assets", "photomancer_icon_256.png"))
    if not values:
        return 1
    config.update_config({k: values[k] for k in ("photo_root", "data_dir", "backup_dir")})
    if values["desktop"] or values["menu"]:
        make_shortcuts(values["desktop"], values["menu"])
    return 0


def main():
    if not config.exists() and run_wizard() != 0:
        return
    _init_theme()
    run_app(App, SPEC)


if __name__ == "__main__":
    main()
