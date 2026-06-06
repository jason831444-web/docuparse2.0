FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG INSTALL_LLAMA_CPP_DEPS=false
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libgl1 \
        libglib2.0-0 \
        $([ "$INSTALL_LLAMA_CPP_DEPS" = "true" ] && echo "build-essential cmake") \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-constraints.txt requirements-ocr.txt requirements-ai.txt requirements-llama.txt ./
RUN pip install -c requirements-constraints.txt -r requirements.txt \
    && python - <<'PY'
import cv2
import numpy
print("dependency_check_base", "numpy", numpy.__version__, "cv2", cv2.__version__)
assert numpy.__version__.startswith("1.26."), numpy.__version__
PY
ARG INSTALL_OCR_DEPS=true
RUN if [ "$INSTALL_OCR_DEPS" = "true" ]; then pip install -c requirements-constraints.txt -r requirements-ocr.txt; fi \
    && python - <<'PY'
import cv2
import numpy
print("dependency_check_ocr", "numpy", numpy.__version__, "cv2", cv2.__version__)
assert numpy.__version__.startswith("1.26."), numpy.__version__
PY
ARG INSTALL_AI_DEPS=false
RUN if [ "$INSTALL_AI_DEPS" = "true" ]; then pip install -c requirements-constraints.txt -r requirements-ai.txt; fi \
    && python - <<'PY'
import numpy
print("dependency_check_ai", "numpy", numpy.__version__)
assert numpy.__version__.startswith("1.26."), numpy.__version__
PY
RUN if [ "$INSTALL_LLAMA_CPP_DEPS" = "true" ]; then pip install -c requirements-constraints.txt -r requirements-llama.txt; fi

COPY . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
