FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --no-create-home worker
COPY main.py stage_assets.py render_frames.py stage_project_bundle.py ./
COPY adapters ./adapters
RUN chmod -R a+rX /app
RUN mkdir -p /data/artifacts && chown -R worker:worker /data
USER 10001
CMD ["python", "main.py"]
