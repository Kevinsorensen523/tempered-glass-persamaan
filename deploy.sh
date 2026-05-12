#!/bin/bash
# Setup & deploy Database Tempered Glass di Linux (Ubuntu/Debian)
set -e

echo "=== Install dependencies ==="
sudo apt update -y
sudo apt install -y python3 python3-pip python3-venv

echo "=== Buat virtual environment ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Install packages ==="
pip install flask gunicorn

echo "=== Selesai. Jalankan dengan: ==="
echo "  ./run.sh"
