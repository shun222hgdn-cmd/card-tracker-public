"""Vercelの入口。Vercelはこのファイルの `app` (WSGIアプリ)を呼び出す。"""
import sys
from html import escape
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cardtracker import create_app  # noqa: E402


def build_app() -> Flask:
    try:
        return create_app()
    except RuntimeError as e:
        # 環境変数が足りないときは500で落とさず、足りない変数の名前だけを表示する(値は表示しない)
        fallback = Flask(__name__)
        page = f"<meta charset='utf-8'><title>セットアップ中</title><h1>セットアップ中です</h1><p>{escape(str(e))}</p>"
        fallback.add_url_rule("/", "pending", lambda path="": (page, 503))
        fallback.add_url_rule("/<path:path>", "pending")
        return fallback


app = build_app()  # Vercelが静的に検出するので、ファイルの一番外側で定義する
