FROM python:3.12-slim

# tesseract-ocr provides the OCR binary pytesseract shells out to.
# libgl1/libglib2.0-0 are Pillow runtime deps for some image formats.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
# face-recognition declares a hard dependency on `dlib`, which is source-only and
# would trigger a multi-minute cmake build. Everything else (including the
# prebuilt `dlib-bin` that satisfies `import dlib`) installs normally first, then
# face-recognition goes in with --no-deps so pip cannot pull the source dlib back.
#
# The long timeout is not padding: face_recognition_models is a ~100 MB sdist and
# pip's 15 s default read timeout aborts it mid-transfer on a slow link. The
# truncated file then fails its hash check, which reads as "corrupt package on
# PyPI" when the real cause is the download never finishing.
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt \
    && pip install --no-cache-dir --no-deps face-recognition==1.3.0

# Fail the build rather than the request: a missing model file here would
# otherwise surface as a 503 at claim time, long after anyone was watching.
RUN python -c "import dlib, face_recognition, face_recognition_models, os; \
    p = face_recognition_models.face_recognition_model_location(); \
    assert os.path.exists(p), 'face model missing: ' + p; \
    print('face backend ready:', dlib.__version__)"

COPY . .

RUN mkdir -p /data/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
