"""Vercelの入口。Vercelはこのファイルの `app` (WSGIアプリ)を呼び出す。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cardtracker import create_app  # noqa: E402

app = create_app()  # Vercelが静的に検出するので、ファイルの一番外側で定義する
