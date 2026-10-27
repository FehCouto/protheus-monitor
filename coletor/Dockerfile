FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY coletor.py .
RUN useradd --system coletor && mkdir -p /app/estado && chown coletor /app/estado
USER coletor

CMD ["python", "coletor.py"]
