#!/bin/bash
# Jalankan server production dengan gunicorn
source venv/bin/activate
exec gunicorn app:app \
  --bind 0.0.0.0:8080 \
  --workers 2 \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
