"""pytest 収集前にテスト用の設定ファイルパスを固定する。"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = Path(__file__).resolve().parent / "fixtures"

os.environ["SECRETS_FILE"] = str(_FIXTURES / "test_secrets.yaml")
os.environ["CONFIG_FILE"] = str(_ROOT / "config.yaml")
os.environ["POSTED_FILE"] = str(_FIXTURES / "posted_entries.txt")
