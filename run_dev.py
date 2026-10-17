"""ローカル開発用の起動スクリプト。ダミーログイン・ダミーGmailで動く。

    .venv\Scripts\python run_dev.py   →  http://localhost:5050
"""
import os

os.environ.setdefault("DEV_MODE", "1")

from cardtracker import create_app  # noqa: E402

if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), debug=True)
