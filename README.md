---
title: GemSearch
emoji: 💎
colorFrom: purple
colorTo: blue
sdk: docker
pinned: false
app_port: 7860
---

# GemSearch — AI Jewellery Visual Search

OpenCLIP ViT-B-32 + Qdrant Cloud + FastAPI. Upload any jewellery image and find visually similar pieces instantly.

## Environment Variables (set in HF Space Settings → Variables)

| Key | Description |
|-----|-------------|
| `QDRANT_URL` | Your Qdrant Cloud cluster URL |
| `QDRANT_API_KEY` | Qdrant Cloud API key (mark as secret) |
| `CLIP_MODEL` | `ViT-B-32` |
| `CLIP_PRETRAINED` | `openai` |
| `REMBG_PREPROCESSING` | `false` (set same value used when indexing) |

## Local Development

```powershell
# Install deps
pip install torch==2.2.2 torchvision==0.17.2 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Start local Qdrant (or point QDRANT_URL to Qdrant Cloud in .env)
docker run -d -p 6333:6333 --name qdrant qdrant/qdrant

# Index dataset to Qdrant Cloud
python setup_dataset.py

# Run server
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Open: http://localhost:8000
