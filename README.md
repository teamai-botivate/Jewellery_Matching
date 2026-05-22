# GemSearch — AI Jewellery Visual Search System

Gemini-powered image similarity search for jewellery, built with FastAPI + Qdrant.

## Quick Start

### 1. Add your Gemini API key
```
# edit .env
GEMINI_API_KEY=AIza...your-key-here
```

### 2. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 3. Start Qdrant server (Docker required — do this FIRST)

Linux / macOS / Git-Bash:
```bash
docker run -d -p 6333:6333 \
    -v "$(pwd)/qdrant_storage:/qdrant/storage" \
    --name qdrant qdrant/qdrant
```

Windows PowerShell:
```powershell
docker run -d -p 6333:6333 `
    -v "${PWD}/qdrant_storage:/qdrant/storage" `
    --name qdrant qdrant/qdrant
```

To restart a stopped container on subsequent runs:
```bash
docker start qdrant
```

### 4. Run the FastAPI backend
```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### 5. (Optional) Download + index the Kaggle dataset
```bash
python setup_dataset.py
```
> Qdrant must already be running before you run this.

### 6. Open the UI
```
http://localhost:8000
```

---

## Why server mode instead of embedded?

Qdrant's embedded file-based client (`QdrantClient(path=...)`) holds an exclusive
lock on the storage folder.  If two processes (the FastAPI server **and**
`setup_dataset.py`) both try to open the same folder, the second one raises:

```
RuntimeError: Storage folder qdrant_storage is already accessed by another instance
```

Using `QdrantClient(host="localhost", port=6333)` connects over HTTP to a single
shared Qdrant server process.  Any number of clients can connect simultaneously
with zero lock conflicts.  Data persists in `qdrant_storage/` via the Docker
volume mount.

---

## Dataset Setup (optional)

Place jewellery images inside `dataset/` organised by category:
```
dataset/
  rings/
    ring1.jpg
    ring2.png
  necklaces/
    neck1.jpg
  earrings/
    ear1.webp
```

Then click **"Start Dataset Indexing"** in the Admin Panel, or call:
```bash
curl -X POST http://localhost:8000/index-dataset
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| POST | `/search` | Upload image → top-12 similar results |
| POST | `/admin/upload` | Upload + embed + store a new jewellery image |
| POST | `/index-dataset` | Batch-index all images in `dataset/` (background) |
| GET  | `/stats` | DB + vector counts, category breakdown |
| GET  | `/health` | Health check |
| GET  | `/images/{filename}` | Serve an image by filename |

---

## Notes on Qdrant

- Vectors are stored in `qdrant_storage/` using the **local file-based** mode (no Docker needed for storage, but the server is still needed to serve the API).
- Alternatively, to use an **in-process** embedded Qdrant (no Docker at all), change `app.py` line:
  ```python
  qdrant = QdrantClient(path=str(QDRANT_DIR))
  ```
  to:
  ```python
  qdrant = QdrantClient(":memory:")   # ephemeral, loses data on restart
  ```

---

## Project Structure
```
project/
├── app.py              ← FastAPI backend (single file)
├── requirements.txt
├── .env                ← GEMINI_API_KEY
├── jewellery.db        ← SQLite (auto-created)
├── uploads/            ← Admin-uploaded images
├── dataset/            ← Your jewellery dataset
├── qdrant_storage/     ← Qdrant vector data
└── frontend/
    ├── index.html
    ├── style.css
    └── script.js
```
