"""管理用コマンド(要件 6-3: バックアップ・復元)。

    flask --app cardtracker backup backups/2026-10-17.json
    flask --app cardtracker restore backups/2026-10-17.json
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import click
from sqlalchemy import delete, insert, select

from .db import Base, db_session

TABLE_ORDER = ["users", "card_settings", "exclusion_rules", "transactions"]  # 復元時は親テーブルから順に入れる
# JSONでは文字列になる型を、復元時に元の型へ戻す
PARSERS = {datetime: datetime.fromisoformat, date: date.fromisoformat, Decimal: Decimal}


def register(app) -> None:
    @app.cli.command("backup")
    @click.argument("path")
    def backup(path):
        """全テーブルをJSONに書き出す。家族の明細を含むので、ファイルの扱いに注意。"""
        tables = Base.metadata.tables
        data = {name: [dict(row._mapping) for row in db_session.execute(select(tables[name]))] for name in TABLE_ORDER}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        click.echo(f"保存しました: {path} (" + ", ".join(f"{k} {len(v)}件" for k, v in data.items()) + ")")

    @app.cli.command("restore")
    @click.argument("path")
    @click.option("--yes", is_flag=True, help="確認なしで上書きする")
    def restore(path, yes):
        """バックアップJSONで全テーブルを置き換える。"""
        if not yes:
            click.confirm("現在のデータをすべて置き換えます。よろしいですか?", abort=True)
        data, tables = json.loads(Path(path).read_text(encoding="utf-8")), Base.metadata.tables
        for name in reversed(TABLE_ORDER):
            db_session.execute(delete(tables[name]))
        for name in TABLE_ORDER:
            columns = tables[name].c
            rows = [{k: v if v is None else PARSERS.get(columns[k].type.python_type, lambda x: x)(v) for k, v in row.items()}
                    for row in data.get(name, [])]
            if rows:
                db_session.execute(insert(tables[name]), rows)
        db_session.commit()
        click.echo("復元しました。Postgresの場合は、IDの連番がずれないよう README の手順で調整してください。")
