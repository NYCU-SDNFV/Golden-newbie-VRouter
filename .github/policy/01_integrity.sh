#!/usr/bin/env bash
# Base 規範 #2 — 受保護檔案完整性
# 比對 .github/policy/manifest.sha256。清單由 Lab 作者以 gen-manifest.sh 產生。
set -euo pipefail
_HERE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
exec python3 "$_HERE/integrity.py" --manifest "$_HERE/manifest.sha256"
