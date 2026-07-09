"""pytest 収集前にテスト用の設定ファイルパスを固定する。"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = Path(__file__).resolve().parent / "fixtures"

os.environ.setdefault("SECRETS_FILE", str(_FIXTURES / "secrets.yaml"))
os.environ.setdefault("CONFIG_FILE", str(_ROOT / "config.yaml"))
os.environ.setdefault("POSTED_FILE", str(_FIXTURES / "posted_entries.txt"))
