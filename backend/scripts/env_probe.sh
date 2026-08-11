#!/bin/sh
# Report which optional backends are present in this container.
echo "--- python ---"
python --version
echo "--- face_recognition ---"
python - <<'PY'
try:
    import face_recognition, numpy
    print("face_recognition OK, numpy", numpy.__version__)
except Exception as exc:
    print("face_recognition MISSING:", type(exc).__name__, exc)
PY
echo "--- pytesseract ---"
python - <<'PY'
try:
    import pytesseract
    print("pytesseract OK")
except Exception as exc:
    print("pytesseract MISSING:", type(exc).__name__, exc)
PY
echo "--- tesseract binary ---"
tesseract --version 2>&1 | head -2 || echo "tesseract binary MISSING"
echo "--- pytest plugins ---"
python -m pytest --version 2>&1 | head -5
