FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install playwright && playwright install --with-deps chromium

WORKDIR /foto
COPY foto.py .
CMD ["python", "foto.py"]
