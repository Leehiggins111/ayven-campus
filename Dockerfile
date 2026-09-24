FROM python:3.12-slim
WORKDIR /app
COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY apps/api /app
ENV AYVEN_LLM_STUB=1
ENV AYVEN_DB=/data/ayven-campus.db
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["sh", "-c", "mkdir -p /data && python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
