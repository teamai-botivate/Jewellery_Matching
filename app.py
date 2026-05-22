"""
AI Jewellery Visual Search System
FastAPI Backend - OpenCLIP + Qdrant + SQLite + rembg + Feedback
"""

import os
import sqlite3
import uuid
import logging
import time
from pathlib import Path
from io import BytesIO
from typing import Optional

import torch
import open_clip
import numpy as np
from PIL import Image
import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

# -- Optional rembg (graceful degradation if not installed) ---------------
try:
    from rembg import remove as _rembg_remove, new_session as _rembg_new_session
    REMBG_AVAILABLE = True
except ImportError:
    REMBG_AVAILABLE = False

# ---------------------------------------------
# Config
# ---------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

if not REMBG_AVAILABLE:
    log.warning("rembg not installed - background removal disabled. Run: pip install rembg")

BASE_DIR     = Path(__file__).parent
UPLOADS_DIR  = BASE_DIR / "uploads"
DATASET_DIR  = BASE_DIR / "dataset"
QDRANT_DIR   = BASE_DIR / "qdrant_storage"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH      = BASE_DIR / "jewellery.db"

COLLECTION_NAME    = "jewellery_collection"
TOP_K              = 12
TOP_K_BROAD        = 40    # broad pass for category voting
SIMILARITY_THRESHOLD = 0.68  # discard results below this score (0–1)
CAT_CONFIDENCE_MIN   = 0.50  # auto-apply category filter only above this confidence
MAX_UPLOAD_MB      = 10
ALLOWED_EXTS       = {".jpg", ".jpeg", ".png", ".webp"}

QDRANT_HOST        = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT        = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_URL         = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY     = os.getenv("QDRANT_API_KEY", "")
QDRANT_RETRIES     = 6
QDRANT_RETRY_DELAY = 3

CLIP_MODEL_NAME     = os.getenv("CLIP_MODEL", "ViT-B-32")
CLIP_PRETRAINED     = os.getenv("CLIP_PRETRAINED", "openai")
# System-wide preprocessing flag — apply rembg at ALL three places (index/admin/search)
# Change in .env and re-index when switching modes
REMBG_PREPROCESSING = os.getenv("REMBG_PREPROCESSING", "true").lower() == "true"

for d in [UPLOADS_DIR, DATASET_DIR, QDRANT_DIR, FRONTEND_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------
# FastAPI App
# ---------------------------------------------
app = FastAPI(
    title="AI Jewellery Visual Search",
    description="OpenCLIP-powered visual jewellery search",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads",  StaticFiles(directory=str(UPLOADS_DIR)),              name="uploads")
app.mount("/dataset",  StaticFiles(directory=str(DATASET_DIR), html=False),  name="dataset")
app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR), html=True),  name="frontend")

# ---------------------------------------------
# OpenCLIP - Global Singleton
# ---------------------------------------------
clip_model      = None
clip_preprocess = None
device          = "cuda" if torch.cuda.is_available() else "cpu"


def load_clip_model():
    global clip_model, clip_preprocess
    if clip_model is not None:
        return
    log.info("Loading OpenCLIP %s [%s] on %s ...", CLIP_MODEL_NAME, CLIP_PRETRAINED, device)
    model, _, preprocess = open_clip.create_model_and_transforms(
        CLIP_MODEL_NAME, pretrained=CLIP_PRETRAINED
    )
    clip_model      = model.to(device).eval()
    clip_preprocess = preprocess
    log.info("OpenCLIP model loaded.")


def generate_embedding(pil_image: Image.Image) -> list:
    load_clip_model()
    tensor = clip_preprocess(pil_image).unsqueeze(0).to(device)
    with torch.no_grad():
        emb = clip_model.encode_image(tensor)
        emb /= emb.norm(dim=-1, keepdim=True)
    return emb[0].cpu().tolist()


def get_embedding_dim() -> int:
    load_clip_model()
    dummy = Image.new("RGB", (64, 64), color=128)
    return len(generate_embedding(dummy))

# ---------------------------------------------
# rembg - Background Removal
# ---------------------------------------------
_rembg_session = None


def get_rembg_session():
    global _rembg_session
    if _rembg_session is None and REMBG_AVAILABLE:
        log.info("Loading rembg u2net model...")
        _rembg_session = _rembg_new_session("u2net")
        log.info("rembg model ready.")
    return _rembg_session


def remove_background(pil_image: Image.Image) -> Image.Image:
    """Remove image background, return on white canvas."""
    session = get_rembg_session()
    if session is None:
        return pil_image
    output = _rembg_remove(pil_image, session=session)
    bg = Image.new("RGB", output.size, (255, 255, 255))
    if output.mode == "RGBA":
        bg.paste(output, mask=output.split()[3])
    else:
        bg = output.convert("RGB")
    return bg


def detect_metal_color(pil_image: Image.Image) -> str:
    """
    Detect dominant metal color from a jewellery image.
    Best called AFTER background removal so only the jewellery pixels are analysed.
    Returns: 'gold' | 'silver' | 'rose_gold' | 'other'
    """
    import colorsys
    try:
        img = pil_image.convert("RGB")
        pixels = np.array(img, dtype=np.float32)
        r, g, b = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]

        # Exclude near-white background (R,G,B > 235) and near-black shadows
        mask = ~((r > 235) & (g > 235) & (b > 235))
        mask &= ((r + g + b) > 90)

        if mask.sum() < 200:
            return "other"

        rm = float(r[mask].mean())
        gm = float(g[mask].mean())
        bm = float(b[mask].mean())

        h, s, v = colorsys.rgb_to_hsv(rm / 255, gm / 255, bm / 255)

        # Silver / white gold: very low saturation, mid-to-high brightness
        if s < 0.18 and v > 0.35:
            return "silver"
        # Yellow gold: yellow hue (40°–65°), meaningful saturation
        if 0.11 <= h <= 0.18 and s >= 0.25:
            return "gold"
        # Rose gold: warm red-orange hue (0°–30° or 330°–360°), moderate saturation
        if (h < 0.085 or h > 0.91) and s >= 0.18:
            return "rose_gold"
        # Broad warm yellow catch (some gold photos are desaturated)
        if 0.08 <= h <= 0.20 and s >= 0.15:
            return "gold"
        return "other"
    except Exception:
        return "other"


def _infer_category_from_hits(hits: list) -> tuple[str, float]:
    """
    Infer dominant category from already-fetched search results.
    Weights each hit by score² so near-exact matches dominate the vote.
    No extra Qdrant call required.
    """
    from collections import defaultdict
    if not hits:
        return "all", 0.0
    cat_scores: dict = defaultdict(float)
    for h in hits:
        cat = h.payload.get("category", "unknown")
        cat_scores[cat] += h.score ** 2
    total    = sum(cat_scores.values())
    dominant = max(cat_scores, key=cat_scores.get)
    confidence = cat_scores[dominant] / total if total > 0 else 0.0
    return dominant, round(confidence, 3)

# ---------------------------------------------
# SQLite
# ---------------------------------------------
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jewellery (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT NOT NULL,
                image_path  TEXT NOT NULL,
                category    TEXT DEFAULT 'uncategorized',
                upload_type TEXT DEFAULT 'dataset',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    log.info("SQLite ready: %s", DB_PATH)


def init_feedback_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                query_file    TEXT,
                result_file   TEXT,
                result_cat    TEXT,
                relevant      INTEGER DEFAULT 0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    log.info("Feedback table ready.")


def db_insert(filename: str, image_path: str, category: str, upload_type: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO jewellery (filename, image_path, category, upload_type) VALUES (?,?,?,?)",
            (filename, image_path, category, upload_type),
        )
        conn.commit()
        return cur.lastrowid


def db_feedback_insert(query_file: str, result_file: str, result_cat: str, relevant: bool):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO feedback (query_file, result_file, result_cat, relevant) VALUES (?,?,?,?)",
            (query_file, result_file, result_cat, int(relevant)),
        )
        conn.commit()


def db_stats() -> dict:
    with get_db() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM jewellery").fetchone()[0]
        cats   = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM jewellery GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT filename, category, upload_type, created_at FROM jewellery ORDER BY id DESC LIMIT 10"
        ).fetchall()
    return {
        "total":      total,
        "categories": [{"name": r["category"], "count": r["cnt"]} for r in cats],
        "recent":     [dict(r) for r in recent],
    }


def db_accuracy_stats() -> dict:
    with get_db() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        relevant = conn.execute("SELECT COUNT(*) FROM feedback WHERE relevant=1").fetchone()[0]
        by_cat   = conn.execute("""
            SELECT result_cat,
                   COUNT(*)    AS total,
                   SUM(relevant) AS rel
            FROM   feedback
            GROUP  BY result_cat
            ORDER  BY total DESC
        """).fetchall()
    precision = round(relevant / total * 100, 1) if total > 0 else 0
    return {
        "total_ratings": total,
        "relevant":      relevant,
        "precision_pct": precision,
        "by_category": [
            {
                "category":  r["result_cat"],
                "total":     r["total"],
                "relevant":  r["rel"],
                "precision": round(r["rel"] / r["total"] * 100, 1) if r["total"] > 0 else 0,
            }
            for r in by_cat
        ],
    }

# ---------------------------------------------
# Qdrant - Server Mode
# ---------------------------------------------
qdrant: Optional[QdrantClient] = None

_QDRANT_HINT = (
    "\n\nQdrant is not running.\n"
    "  Windows: .\\start_qdrant.ps1\n"
    "  Docker:  docker run -d -p 6333:6333 --name qdrant qdrant/qdrant\n"
    "  Restart: docker start qdrant\n"
)


def _qdrant_connect_once() -> QdrantClient:
    if QDRANT_URL:
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=30)
    else:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)
    client.get_collections()
    return client


def init_qdrant():
    global qdrant
    last_err: Exception = RuntimeError("unknown")

    for attempt in range(1, QDRANT_RETRIES + 1):
        try:
            client  = _qdrant_connect_once()
            emb_dim = get_embedding_dim()
            existing = [c.name for c in client.get_collections().collections]

            if COLLECTION_NAME in existing:
                info         = client.get_collection(COLLECTION_NAME)
                existing_dim = info.config.params.vectors.size
                if existing_dim != emb_dim:
                    log.warning(
                        "Dim mismatch: collection=%d model=%d -> recreating.",
                        existing_dim, emb_dim,
                    )
                    client.delete_collection(COLLECTION_NAME)
                    existing = []

            if COLLECTION_NAME not in existing:
                client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=emb_dim, distance=Distance.COSINE),
                )
                log.info("Qdrant collection '%s' created (dim=%d)", COLLECTION_NAME, emb_dim)
            else:
                log.info("Qdrant collection '%s' ready (dim=%d)", COLLECTION_NAME, emb_dim)

            qdrant = client
            log.info("Qdrant connected: %s", QDRANT_URL if QDRANT_URL else f"{QDRANT_HOST}:{QDRANT_PORT}")
            return

        except Exception as exc:
            last_err = exc
            log.warning("Qdrant attempt %d/%d failed: %s", attempt, QDRANT_RETRIES, exc)
            if attempt < QDRANT_RETRIES:
                time.sleep(QDRANT_RETRY_DELAY)

    raise RuntimeError(
        f"Cannot connect to Qdrant at {QDRANT_HOST}:{QDRANT_PORT} "
        f"after {QDRANT_RETRIES} attempts. {last_err}" + _QDRANT_HINT
    )


def qdrant_health_check() -> bool:
    try:
        _qdrant_connect_once()
        return True
    except Exception:
        return False


def qdrant_upsert(point_id: str, vector: list, payload: dict):
    qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )


def qdrant_search(query_vector: list, top_k: int = TOP_K,
                  metal_color: Optional[str] = None,
                  category: Optional[str] = None) -> list:
    conditions = []
    if metal_color and metal_color not in ("all", "other", ""):
        conditions.append(FieldCondition(key="metal_color", match=MatchValue(value=metal_color)))
    if category and category != "all":
        conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
    query_filter = Filter(must=conditions) if conditions else None
    return qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    )


def qdrant_count() -> int:
    info = qdrant.get_collection(COLLECTION_NAME)
    # points_count is reliable in qdrant-client 1.9+; vectors_count is deprecated/None in 1.12+
    return int(getattr(info, "points_count", None) or getattr(info, "vectors_count", None) or 0)

# ---------------------------------------------
# Image Utilities
# ---------------------------------------------
def validate_and_open(data: bytes, raise_http: bool = False) -> Image.Image:
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        msg = f"File exceeds {MAX_UPLOAD_MB} MB limit"
        raise HTTPException(status_code=413, detail=msg) if raise_http else ValueError(msg)
    try:
        return Image.open(BytesIO(data)).convert("RGB")
    except Exception:
        msg = "Invalid or corrupt image file"
        raise HTTPException(status_code=400, detail=msg) if raise_http else ValueError(msg)


def save_upload(data: bytes, original_filename: str, subdir: Path) -> tuple:
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Extension '{ext}' not allowed")
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = subdir / unique_name
    dest.write_bytes(data)
    return unique_name, dest


def infer_category(image_path: Path) -> str:
    parts = image_path.parts
    try:
        idx = parts.index("dataset")
        if idx + 1 < len(parts) - 1:
            return parts[idx + 1]
    except ValueError:
        pass
    return "uncategorized"

# ---------------------------------------------
# Core Index Helper
# ---------------------------------------------
def index_single_image(image_path: Path, upload_type: str = "dataset") -> dict:
    data = image_path.read_bytes()
    img  = validate_and_open(data)

    # System-wide: apply rembg when configured and available
    rembg_applied = False
    if REMBG_PREPROCESSING and REMBG_AVAILABLE:
        img           = remove_background(img)
        rembg_applied = True

    metal_color = detect_metal_color(img)
    embedding   = generate_embedding(img)
    category    = infer_category(image_path)
    filename    = image_path.name

    try:
        rel      = image_path.relative_to(BASE_DIR)
        url_path = "/" + str(rel).replace("\\", "/")
    except ValueError:
        url_path = "/uploads/" + filename

    row_id   = db_insert(filename, url_path, category, upload_type)
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url_path))

    qdrant_upsert(
        point_id=point_id,
        vector=embedding,
        payload={
            "image_path":  url_path,
            "filename":    filename,
            "category":    category,
            "upload_type": upload_type,
            "db_id":       row_id,
            "rembg_used":  rembg_applied,
            "metal_color": metal_color,
        },
    )
    return {
        "filename":    filename,
        "category":    category,
        "metal_color": metal_color,
        "point_id":    point_id,
    }

# ---------------------------------------------
# Qdrant → SQLite sync (runs on fresh deployments where SQLite is empty)
# ---------------------------------------------
def sync_sqlite_from_qdrant():
    try:
        with get_db() as conn:
            if conn.execute("SELECT COUNT(*) FROM jewellery").fetchone()[0] > 0:
                return  # SQLite already populated
        vec_count = qdrant_count()
        if vec_count == 0:
            return
        log.info("SQLite empty, Qdrant has %d vectors — syncing metadata...", vec_count)
        offset = None
        synced = 0
        while True:
            points, next_offset = qdrant.scroll(
                collection_name=COLLECTION_NAME,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for pt in points:
                p = pt.payload or {}
                with get_db() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO jewellery (filename, image_path, category, upload_type) VALUES (?,?,?,?)",
                        (p.get("filename", ""), p.get("image_path", ""),
                         p.get("category", "uncategorized"), p.get("upload_type", "dataset")),
                    )
                    conn.commit()
                synced += 1
            if next_offset is None:
                break
            offset = next_offset
        log.info("Synced %d records from Qdrant → SQLite", synced)
    except Exception as exc:
        log.warning("Qdrant→SQLite sync failed (non-fatal): %s", exc)


# ---------------------------------------------
# Startup
# ---------------------------------------------
@app.on_event("startup")
async def startup_event():
    init_db()
    init_feedback_db()
    load_clip_model()
    init_qdrant()
    sync_sqlite_from_qdrant()
    log.info(
        "System ready. device=%s  vectors=%d  rembg=%s",
        device, qdrant_count(), REMBG_AVAILABLE,
    )

# ---------------------------------------------
# Pydantic Models
# ---------------------------------------------
class FeedbackIn(BaseModel):
    query_filename:  str
    result_filename: str
    result_category: str
    relevant:        bool

# ---------------------------------------------
# Routes
# ---------------------------------------------
@app.get("/", include_in_schema=False)
async def root():
    idx = FRONTEND_DIR / "index.html"
    return FileResponse(str(idx)) if idx.exists() else JSONResponse(
        {"message": "AI Jewellery Visual Search", "docs": "/docs"}
    )


@app.get("/health")
async def health():
    return {
        "status":          "ok",
        "clip_model":      CLIP_MODEL_NAME,
        "device":          device,
        "vectors":         qdrant_count(),
        "rembg_available": REMBG_AVAILABLE,
        "db":              str(DB_PATH),
    }


@app.get("/stats")
async def stats():
    s = db_stats()
    s["qdrant_vectors"] = qdrant_count()
    return s


@app.post("/search")
async def search(file: UploadFile = File(...)):
    data = await file.read()
    img  = validate_and_open(data, raise_http=True)

    rembg_applied = False
    if REMBG_PREPROCESSING and REMBG_AVAILABLE:
        img           = remove_background(img)
        rembg_applied = True

    embedding = generate_embedding(img)

    # ── Single Qdrant call — no double round-trip ──────────────────────────
    # Fetch extra so re-ranking has enough candidates
    hits = qdrant_search(embedding, top_k=TOP_K * 3)

    if not hits:
        return {"results": [], "total_found": 0, "rembg_active": rembg_applied,
                "detected_category": "all", "cat_confidence": 0, "fallback_used": False}

    # ── Infer dominant category from results (free — uses already-fetched hits) ──
    detected_cat, cat_confidence = _infer_category_from_hits(hits)

    # ── Smart re-rank ──────────────────────────────────────────────────────
    # Always keep top-3 by raw score (guarantees exact/near-exact match stays first).
    # Fill remaining slots: dominant-category items first, then others — so similar
    # type bubbles up without hard-filtering anything out.
    N_EXACT   = 3
    top_exact = hits[:N_EXACT]
    rest      = hits[N_EXACT:]

    if cat_confidence >= CAT_CONFIDENCE_MIN:
        dom_rest   = [h for h in rest if h.payload.get("category") == detected_cat]
        other_rest = [h for h in rest if h.payload.get("category") != detected_cat]
        reranked   = top_exact + dom_rest + other_rest
    else:
        reranked = hits  # low confidence → trust Qdrant cosine ordering

    # ── Similarity threshold — drop clearly unrelated results ──────────────
    hits_above = [h for h in reranked if h.score >= SIMILARITY_THRESHOLD]
    fallback_used = False
    if not hits_above:
        hits_above    = reranked
        fallback_used = True

    final_hits = hits_above[:TOP_K]

    results = [
        {
            "image_url":   h.payload.get("image_path",  ""),
            "filename":    h.payload.get("filename",    ""),
            "category":    h.payload.get("category",    "unknown"),
            "similarity":  round(h.score * 100, 2),
            "metal_color": h.payload.get("metal_color", "other"),
        }
        for h in final_hits
    ]

    return {
        "results":           results,
        "query_image":       file.filename,
        "total_found":       len(results),
        "rembg_active":      rembg_applied,
        "detected_category": detected_cat,
        "cat_confidence":    round(cat_confidence * 100, 1),
        "fallback_used":     fallback_used,
    }


@app.post("/admin/upload")
async def admin_upload(
    file:     UploadFile = File(...),
    category: str        = "uncategorized",
):
    data = await file.read()

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Extension '{ext}' not allowed")

    unique_name, _ = save_upload(data, file.filename, UPLOADS_DIR)
    img = validate_and_open(data, raise_http=True)

    # System-wide: apply rembg consistently with dataset indexing
    rembg_applied = False
    if REMBG_PREPROCESSING and REMBG_AVAILABLE:
        log.info("Removing background before embedding: %s", unique_name)
        img           = remove_background(img)
        rembg_applied = True

    metal_color = detect_metal_color(img)
    embedding   = generate_embedding(img)
    url_path    = f"/uploads/{unique_name}"
    row_id      = db_insert(unique_name, url_path, category, "admin")
    point_id    = str(uuid.uuid5(uuid.NAMESPACE_URL, url_path))

    qdrant_upsert(
        point_id=point_id,
        vector=embedding,
        payload={
            "image_path":  url_path,
            "filename":    unique_name,
            "category":    category,
            "upload_type": "admin",
            "db_id":       row_id,
            "rembg_used":  rembg_applied,
            "metal_color": metal_color,
        },
    )
    return {
        "success":     True,
        "filename":    unique_name,
        "url":         url_path,
        "category":    category,
        "metal_color": metal_color,
        "db_id":       row_id,
        "point_id":    point_id,
        "rembg_used":  rembg_applied,
        "message":     "Image indexed and immediately searchable",
    }


@app.post("/index-dataset")
async def index_dataset(background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_indexing)
    return {
        "message":   "Dataset indexing started. Poll /stats for progress.",
        "rembg_mode": REMBG_PREPROCESSING and REMBG_AVAILABLE,
    }


def _run_indexing():
    image_files = []
    for ext in ALLOWED_EXTS:
        image_files.extend(DATASET_DIR.rglob(f"*{ext}"))
        image_files.extend(DATASET_DIR.rglob(f"*{ext.upper()}"))
    log.info(
        "Indexing %d images  rembg=%s ...",
        len(image_files), REMBG_PREPROCESSING and REMBG_AVAILABLE,
    )
    success = failed = 0
    for path in image_files:
        try:
            r = index_single_image(path, upload_type="dataset")
            log.info("Indexed %s  cat=%s  metal=%s", r["filename"], r["category"], r["metal_color"])
            success += 1
        except Exception as exc:
            log.error("Failed %s: %s", path.name, exc)
            failed += 1
    log.info("Done. success=%d  failed=%d", success, failed)


@app.get("/images/{filename}")
async def serve_image(filename: str):
    for d in [UPLOADS_DIR, DATASET_DIR]:
        t = d / filename
        if t.exists():
            return FileResponse(str(t))
    raise HTTPException(status_code=404, detail="Image not found")


# -- Feedback & Accuracy --------------------------------------------------

@app.post("/feedback")
async def submit_feedback(body: FeedbackIn):
    db_feedback_insert(
        body.query_filename,
        body.result_filename,
        body.result_category,
        body.relevant,
    )
    return {"saved": True}


@app.get("/accuracy")
async def get_accuracy():
    return db_accuracy_stats()


@app.get("/rembg-status")
async def rembg_status():
    return {
        "available":    REMBG_AVAILABLE,
        "preprocessing": REMBG_PREPROCESSING and REMBG_AVAILABLE,
    }


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
