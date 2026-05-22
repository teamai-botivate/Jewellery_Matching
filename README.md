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

## Environment Variables (set in HF Space Settings → Variables and Secrets)

| Key | Value | Type |
|-----|-------|------|
| `QDRANT_URL` | Your Qdrant Cloud cluster URL | Variable |
| `QDRANT_API_KEY` | Your Qdrant Cloud API key | **Secret** |
| `CLIP_MODEL` | `ViT-B-32` | Variable |
| `CLIP_PRETRAINED` | `openai` | Variable |
| `REMBG_PREPROCESSING` | `false` | Variable |

## Local Development

```powershell
pip install torch==2.2.2 torchvision==0.17.2 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python setup_dataset.py   # index dataset to Qdrant Cloud
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```
