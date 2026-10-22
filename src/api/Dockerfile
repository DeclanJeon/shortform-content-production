FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --no-create-home app
COPY api/automatic_archive.py api/archive_receipt.py api/image_budget.py api/production_policy.py api/project_bundle.py api/production_projection.py ./
COPY api/main.py api/artifacts.py api/sheet_intake.py api/publish_handoff.py api/creative_automation.py api/creative_bridge.py api/media_automation.py api/review_callback.py api/review_callback_router.py api/review_signature.py ./
COPY creative ./creative
RUN chmod -R a+rX /app
USER 10001
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
