"""
setup_dataset.py
----------------
Pipeline:
  1. Preflight  -- verify Qdrant is running
  2. Download   -- kagglehub
  3. Copy       -- dataset/<category>/
  4. Verify     -- count images per category
  5. Index      -- OpenCLIP embed -> Qdrant + SQLite
  6. Report     -- final counts

Run:
    python setup_dataset.py
"""

import sys
import os
import re as _re
import shutil
import logging
import sqlite3
from pathlib import Path
from collections import defaultdict

# -- Project root -------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# -- Logging ------------------------------------------------------------
LOG_FILE = PROJECT_ROOT / "setup_dataset.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
log = logging.getLogger("setup_dataset")

# -- Load .env before importing app ------------------------------------
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

KAGGLE_TOKEN = os.getenv("KAGGLE_API_TOKEN", "")
if KAGGLE_TOKEN:
    log.info("Kaggle token loaded (%s...)", KAGGLE_TOKEN[:12])
else:
    log.info("KAGGLE_API_TOKEN not in .env -- kagglehub will try ~/.kaggle/access_token")

# -- Import from app.py ------------------------------------------------
log.info("Loading app module...")
from app import (
    init_db,
    init_qdrant,
    qdrant_health_check,
    qdrant_count,
    db_stats,
    index_single_image,
    load_clip_model,
    DATASET_DIR,
    UPLOADS_DIR,
    BASE_DIR,
    ALLOWED_EXTS,
    QDRANT_HOST,
    QDRANT_PORT,
    REMBG_PREPROCESSING,
    REMBG_AVAILABLE,
)

# ======================================================================
# Category detection helpers
# ======================================================================

_KNOWN_CATS = {
    "ring", "rings",
    "necklace", "necklaces",
    "earring", "earrings",
    "pendant", "pendants",
    "bracelet", "bracelets",
    "bangle", "bangles",
    "chain", "chains",
    "anklet", "anklets",
    "brooch", "brooches",
    "watch", "watches",
}

_CAT_PATTERNS = [
    (_re.compile(r"necklace"),           "necklaces"),
    (_re.compile(r"ear(?:ing|ring)?\b"), "earrings"),
    (_re.compile(r"\bear\b"),            "earrings"),
    (_re.compile(r"pendant"),            "pendants"),
    (_re.compile(r"bracelet"),           "bracelets"),
    (_re.compile(r"bangle"),             "bangles"),
    (_re.compile(r"anklet"),             "anklets"),
    (_re.compile(r"chain"),              "chains"),
    (_re.compile(r"brooch"),             "brooches"),
    (_re.compile(r"ring"),               "rings"),
]

_SKIP_STEMS = {
    "avatar", "background", "banner", "logo", "payment", "profile",
    "shopcart", "shopcart4", "shopdetail", "userimage", "cartimg",
    "cartimg4", "jewlery",
}


def _safe_rel(path: Path, base: Path) -> Path:
    try:
        return path.relative_to(base)
    except ValueError:
        return Path(path.name)


def _clean_cat(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _infer_category_from_filename(filename: str) -> str:
    stem = Path(filename).stem.lower()
    for pattern, cat in _CAT_PATTERNS:
        if pattern.search(stem):
            return cat
    if _re.match(r"^\d+$", stem):
        return "jewellery"
    return "uncategorized"


def _resolve_category(rel: Path) -> str:
    for part in rel.parts[:-1]:
        c = _clean_cat(part)
        if c in _KNOWN_CATS:
            return c if c.endswith("s") else c + "s"
    return _infer_category_from_filename(rel.parts[-1])


def _show_tree(root: Path, depth: int = 2, prefix: str = ""):
    if depth == 0:
        return
    try:
        items = sorted(root.iterdir())
    except PermissionError:
        return
    for item in items[:20]:
        icon = "[DIR] " if item.is_dir() else "[IMG] "
        log.info("%s%s%s", prefix, icon, item.name)
        if item.is_dir():
            _show_tree(item, depth - 1, prefix + "   ")


# ======================================================================
# Already indexed check
# ======================================================================

def already_indexed_filenames() -> set:
    try:
        db_path = BASE_DIR / "jewellery.db"
        if not db_path.exists():
            return set()
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT filename FROM jewellery").fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


# ======================================================================
# Separator
# ======================================================================

def sep(title: str = ""):
    bar = "-" * 60
    if title:
        log.info("%s\n  %s\n%s", bar, title, bar)
    else:
        log.info(bar)


# ======================================================================
# Step 0 -- Preflight: Qdrant check
# ======================================================================

def preflight_qdrant():
    sep("PREFLIGHT -- Qdrant server check")
    log.info("Checking Qdrant at %s:%d ...", QDRANT_HOST, QDRANT_PORT)
    if qdrant_health_check():
        log.info("Qdrant is reachable.  Proceeding.")
        return

    log.error("Qdrant is NOT running at %s:%d", QDRANT_HOST, QDRANT_PORT)
    log.error("")
    log.error("Start Qdrant:")
    log.error("  Windows (binary):  .\\start_qdrant.ps1")
    log.error("  Docker:  docker run -d -p 6333:6333 --name qdrant qdrant/qdrant")
    log.error("  Restart: docker start qdrant")
    sys.exit(1)


# ======================================================================
# Step 1 -- Download
# ======================================================================

def step_download() -> Path:
    sep("STEP 1 -- Download Kaggle dataset")

    try:
        import kagglehub
    except ImportError:
        log.error("kagglehub not installed. Run: pip install kagglehub")
        sys.exit(1)

    log.info("kagglehub.dataset_download('harshjangid0015/jewelry-database')")
    log.info("(First run downloads; subsequent runs use local cache)")

    try:
        raw_path = kagglehub.dataset_download("harshjangid0015/jewelry-database")
    except Exception as exc:
        err = str(exc)
        log.error("Download failed: %s", err)
        if "401" in err or "unauthorized" in err.lower() or "token" in err.lower():
            log.error("Auth failed -- check KAGGLE_API_TOKEN in .env")
        elif "404" in err or "not found" in err.lower():
            log.error("Dataset not found -- verify slug: harshjangid0015/jewelry-database")
        sys.exit(1)

    source = Path(raw_path)
    log.info("Dataset at: %s", source)
    return source


# ======================================================================
# Step 2 -- Copy into dataset/<category>/
# ======================================================================

def step_copy(source: Path) -> int:
    sep("STEP 2 -- Copy images into dataset/")

    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    image_files: list = []
    for ext in ALLOWED_EXTS:
        image_files.extend(source.rglob(f"*{ext}"))
        image_files.extend(source.rglob(f"*{ext.upper()}"))
    image_files = list({p.resolve(): p for p in image_files}.values())

    log.info("Found %d image files in archive", len(image_files))
    if not image_files:
        log.warning("No images found in %s -- check dataset structure.", source)
        _show_tree(source, depth=3)

    copied = skipped_exists = skipped_asset = 0

    for src_path in image_files:
        filename = src_path.name
        stem     = Path(filename).stem.lower()

        if stem in _SKIP_STEMS:
            skipped_asset += 1
            log.debug("Skip (UI asset): %s", filename)
            continue

        rel      = _safe_rel(src_path, source)
        category = _resolve_category(rel)

        dest_dir = DATASET_DIR / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename

        if dest.exists():
            skipped_exists += 1
        else:
            try:
                shutil.copy2(src_path, dest)
                log.debug("Copied: %s -> dataset/%s/", filename, category)
                copied += 1
            except Exception as exc:
                log.warning("Could not copy %s: %s", src_path, exc)

    log.info(
        "Copied: %d  |  Already present: %d  |  UI assets skipped: %d",
        copied, skipped_exists, skipped_asset,
    )
    return copied + skipped_exists


# ======================================================================
# Step 3 -- Verify
# ======================================================================

def step_verify() -> dict:
    sep("STEP 3 -- Verify dataset/")

    cats    = sorted([d for d in DATASET_DIR.iterdir() if d.is_dir()])
    summary = {}
    total   = 0

    for cat_dir in cats:
        count = 0
        for ext in ALLOWED_EXTS:
            count += len(list(cat_dir.glob(f"*{ext}")))
            count += len(list(cat_dir.glob(f"*{ext.upper()}")))
        summary[cat_dir.name] = count
        total += count

    log.info("Dataset path   : %s", DATASET_DIR)
    log.info("Total folders  : %d", len(cats))
    log.info("Total images   : %d", total)
    log.info("")
    for cat, cnt in sorted(summary.items(), key=lambda x: -x[1]):
        bar = "#" * min(cnt // 5 + 1, 40)
        log.info("  %-22s %4d  %s", cat + "/", cnt, bar)
    log.info("")

    return {"total": total, "folders": len(cats), "categories": summary}


# ======================================================================
# Step 4 -- Index: OpenCLIP embed -> Qdrant -> SQLite
# ======================================================================

def step_index() -> dict:
    sep("STEP 4 -- Index images (OpenCLIP -> Qdrant -> SQLite)")

    image_files: list = []
    for ext in ALLOWED_EXTS:
        image_files.extend(DATASET_DIR.rglob(f"*{ext}"))
        image_files.extend(DATASET_DIR.rglob(f"*{ext.upper()}"))
    image_files = list({p.resolve(): p for p in image_files}.values())

    total        = len(image_files)
    success      = 0
    skipped      = 0
    failed       = 0
    errors: list = []

    already_done = already_indexed_filenames()
    log.info("Images in dataset : %d", total)
    log.info("Already indexed   : %d  (will skip)", len(already_done))
    log.info("rembg active      : %s", REMBG_PREPROCESSING and REMBG_AVAILABLE)
    log.info("")

    for i, path in enumerate(image_files, 1):
        filename = path.name

        if filename in already_done:
            log.info("[%d/%d] SKIP  %s", i, total, filename)
            skipped += 1
            continue

        try:
            result = index_single_image(path, upload_type="dataset")
            log.info(
                "[%d/%d] OK  %-40s  cat=%-14s  vec=%s",
                i, total,
                result["filename"][:40],
                result["category"],
                result["point_id"][:8] + "...",
            )
            success += 1
        except Exception as exc:
            log.error("[%d/%d] FAIL  %-40s  %s", i, total, path.name[:40], exc)
            errors.append({"file": str(path), "error": str(exc)})
            failed += 1

    return {
        "total":   total,
        "success": success,
        "skipped": skipped,
        "failed":  failed,
        "errors":  errors,
    }


# ======================================================================
# Step 5 -- Report
# ======================================================================

def step_report(result: dict):
    sep("STEP 5 -- Final Report")

    stats  = db_stats()
    vcount = qdrant_count()

    log.info("============================================================")
    log.info("  Indexed this run   : %d", result["success"])
    log.info("  Skipped (existing) : %d", result["skipped"])
    log.info("  Failed             : %d", result["failed"])
    log.info("  ----------------------------------------------------------")
    log.info("  SQLite total rows  : %d", stats["total"])
    log.info("  Qdrant vectors     : %d", vcount)
    log.info("  Categories in DB   : %d", len(stats["categories"]))
    log.info("============================================================")

    if result["errors"]:
        log.warning("Failed files (%d):", len(result["errors"]))
        for e in result["errors"]:
            log.warning("  %s", e["file"])
            log.warning("    -> %s", e["error"])

    log.info("")
    log.info("All done! Start the server: uvicorn app:app --reload")
    log.info("Open: http://localhost:8000")
    log.info("Full log saved to: %s", LOG_FILE)


# ======================================================================
# Main
# ======================================================================

def preflight_qdrant():  # noqa: F811 (redefined for clarity)
    sep("PREFLIGHT -- Qdrant server check")
    log.info("Checking Qdrant at %s:%d ...", QDRANT_HOST, QDRANT_PORT)
    if qdrant_health_check():
        log.info("Qdrant is reachable.  Proceeding.")
        return
    log.error("Qdrant is NOT running at %s:%d", QDRANT_HOST, QDRANT_PORT)
    log.error("  Windows (binary): .\\start_qdrant.ps1")
    log.error("  Docker:  docker run -d -p 6333:6333 --name qdrant qdrant/qdrant")
    log.error("  Restart: docker start qdrant")
    sys.exit(1)


def main():
    sep("GemSearch v2 -- OpenCLIP Dataset Setup")
    log.info("Project root  : %s", PROJECT_ROOT)
    log.info("Dataset dir   : %s", DATASET_DIR)
    log.info("Qdrant target : %s:%d", QDRANT_HOST, QDRANT_PORT)
    log.info("rembg mode    : preprocessing=%s  available=%s", REMBG_PREPROCESSING, REMBG_AVAILABLE)
    log.info("")

    preflight_qdrant()

    log.info("Initialising SQLite...")
    init_db()

    log.info("Loading OpenCLIP model (first run downloads weights ~350 MB)...")
    load_clip_model()

    log.info("Connecting to Qdrant...")
    init_qdrant()
    log.info("Pre-existing vectors: %d", qdrant_count())

    source_path  = step_download()
    step_copy(source_path)
    step_verify()
    index_result = step_index()
    step_report(index_result)

    return 0 if index_result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
