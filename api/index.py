"""Vercelの入口。Vercelはこのファイルの `app` (WSGIアプリ)を呼び出す。"""
import sys
from html import escape
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cardtracker import create_app  # noqa: E402


def setup_pending_app(error: Exception) -> Flask:
    """環境変数が足りないときは500で落とさず、何が足りないかだけを表示する(値は表示しない)。"""
    fallback = Flask(__name__)
    message = escape(str(error))

    @fallback.route("/", defaults={"path": ""})
    @fallback.route("/<path:path>")
    def setup_pending(path):
        body = (
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>セットアップ中 | カードトラッカー</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:560px;margin:48px auto;padding:0 16px'>"
            "<h1>セットアップ中です</h1>"
            f"<p>サーバーの設定がまだ完了していません。</p><p><code>{message}</code></p>"
            "<p>Vercelの Environment Variables を登録して再デプロイしてください(README「公開手順」参照)。</p>"
        )
        return body, 503

    return fallback


def build_app() -> Flask:
    try:
        return create_app()
    except RuntimeError as e:
        return setup_pending_app(e)


app = build_app()
