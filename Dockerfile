FROM python:3.10-slim

WORKDIR /app

# System libs: libgomp1 is required by onnxruntime (used by rembg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch — much smaller than the default CUDA build
RUN pip install --no-cache-dir \
    torch==2.2.2 torchvision==0.17.2 \
    --index-url https://download.pytorch.org/whl/cpu

# App dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Directories that are gitignored but needed at runtime
RUN mkdir -p uploads dataset

# HuggingFace Spaces uses port 7860; Render passes $PORT
EXPOSE 7860

CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}
