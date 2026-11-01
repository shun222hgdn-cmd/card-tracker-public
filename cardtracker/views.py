"""画面(要件 5. 画面要件)。

すべての取引の取得・変更は「user_id == ログイン中の本人」で絞り込む。
他人の取引IDを直接URLに入れても 404 になる(要件 2-2: サーバー側での権限制御)。
"""
from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import delete, select

from .auth import admin_required, login_required
from .classifier import apply_classification, load_rules, reclassify
from .csv_import import CsvFormatError, import_csv
from .db import db_session
from .models import STATUS_LABELS, STATUSES, ExclusionRule, Transaction, User
from .summary import compute_summary, in_period

bp = Blueprint("main", __name__)


def own_transaction(tx_id: int) -> Transaction:
    tx = db_session.scalar(select(Transaction).where(Transaction.id == tx_id, Transaction.user_id == g.user.id))
    return tx or abort(404)


def parse_yen(raw: str | None) -> int | None:
    """「12,800」「１２８００」などを円の整数にする。空なら None、不正・範囲外なら ValueError。"""
    raw = unicodedata.normalize("NFKC", raw or "").replace(",", "").replace("円", "").strip()
    if not raw:
        return None
    if not 0 <= (value := int(raw)) <= 10_000_000:
        raise ValueError
    return value


def classify_and_save(tx: Transaction) -> None:
    apply_classification(tx, load_rules(db_session), g.user.card_setting.card_label)
    db_session.commit()


# ---------- 個人ホーム (F-05) ----------

@bp.get("/")
@login_required
def home():
    return render_template("home.html", summary=compute_summary(db_session, g.user), setting=g.user.card_setting)


# ---------- 利用履歴 (F-06) ----------

@bp.get("/history")
@login_required
def history():
    status, s = request.args.get("status", "all"), g.user.card_setting
    query = select(Transaction).where(Transaction.user_id == g.user.id)
    if status in STATUSES:
        query = query.where(Transaction.final_status == status)
    rows = db_session.scalars(query.order_by(Transaction.used_at.desc(), Transaction.id.desc()).limit(500))
    return render_template("history.html", status=status,
                           rows=[(tx, in_period(tx, s.period_start, s.period_end)) for tx in rows])


# ---------- 手動修正 (F-04) ----------

@bp.route("/transactions/<int:tx_id>", methods=["GET", "POST"])
@login_required
def edit_transaction(tx_id):
    tx = own_transaction(tx_id)
    if request.method == "GET":
        return render_template("edit.html", tx=tx, labels=STATUS_LABELS)
    if request.form.get("final_status") not in STATUSES:
        abort(400)
    try:
        tx.corrected_amount = parse_yen(request.form.get("corrected_amount"))
    except ValueError:
        flash("集計金額は0〜1,000万円の整数で入力してください。", "error")
        return redirect(url_for("main.edit_transaction", tx_id=tx.id))
    tx.final_status, tx.manual_override = request.form["final_status"], True
    tx.note = (request.form.get("note") or "").strip()[:500] or None
    db_session.commit()
    flash("保存しました。CSVを取り込み直したり、ルールを変えたりしても上書きされません。", "info")
    return redirect(url_for("main.history", status=request.args.get("back", "all")))


@bp.post("/transactions/<int:tx_id>/reset")
@login_required
def reset_transaction(tx_id):
    tx = own_transaction(tx_id)
    tx.manual_override, tx.corrected_amount = False, None
    classify_and_save(tx)
    flash("自動判定に戻しました。", "info")
    return redirect(url_for("main.edit_transaction", tx_id=tx.id))


@bp.post("/transactions/<int:tx_id>/delete")
@login_required
def delete_transaction(tx_id):
    tx = own_transaction(tx_id)
    if tx.source != "manual":
        abort(400, "CSVから取り込んだ取引は削除できません。「対象外」に変更してください。")
    db_session.delete(tx)
    db_session.commit()
    flash("手動登録の取引を削除しました。", "info")
    return redirect(url_for("main.history"))


# ---------- 手動登録 (F-09) ----------

@bp.route("/transactions/new", methods=["GET", "POST"])
@login_required
def new_transaction():
    if request.method == "GET":
        return render_template("new.html", form={})
    form = request.form
    try:
        used_at = datetime.strptime(f"{form.get('date')} {form.get('time') or '00:00'}", "%Y-%m-%d %H:%M")
        amount = parse_yen(form.get("amount"))
    except ValueError:
        amount = None
    merchant = (form.get("merchant") or "").strip()[:200]
    if amount is None or not merchant:
        flash("日付・利用先・金額(円)を正しく入力してください。", "error")
        return render_template("new.html", form=form)
    tx = Transaction(user_id=g.user.id, source="manual", used_at=used_at, merchant=merchant,
                     original_amount=Decimal(amount))
    db_session.add(tx)
    classify_and_save(tx)
    flash(f"登録しました(判定: {tx.status_label})。", "info")
    return redirect(url_for("main.history"))


# ---------- 明細CSVの取り込み (F-02) ----------

@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_statement():
    if request.method == "GET":
        return render_template("import.html")
    file = request.files.get("csv_file")
    try:
        if file is None or not file.filename:
            raise CsvFormatError("CSVファイルを選んでください。")
        added, skipped = import_csv(db_session, g.user, file.read())
    except CsvFormatError as e:
        flash(str(e), "error")
        return redirect(url_for("main.import_statement"))
    flash(f"{added}件を取り込みました。" + (f"取り込み済みの{skipped}件は飛ばしました。" if skipped else ""), "info")
    return redirect(url_for("main.history"))


# ---------- 家族一覧 (F-07) ----------

@bp.get("/family")
@login_required
def family():
    # 集計値(Summary)だけをテンプレートに渡す。明細は渡さない
    users = db_session.scalars(select(User).order_by(User.sort_order))
    return render_template("family.html", summaries=[compute_summary(db_session, u) for u in users])


# ---------- 設定 (F-08, 対象外ルール) ----------

@bp.get("/settings")
@login_required
def settings():
    rules = db_session.scalars(select(ExclusionRule).order_by(ExclusionRule.keyword)).all()
    return render_template("settings.html", setting=g.user.card_setting, rules=rules)


@bp.post("/settings/period")
@login_required
def save_period():
    s = g.user.card_setting
    try:
        start, end = date.fromisoformat(request.form["period_start"]), date.fromisoformat(request.form["period_end"])
    except (KeyError, ValueError):
        start = end = None
    if start is None or end < start or (end - start).days > 400:
        flash("終了日は開始日以降、期間は400日以内の日付にしてください。", "error")
        return redirect(url_for("main.settings"))
    card_label = (request.form.get("card_label") or "").strip()[:100] or None
    label_changed = card_label != s.card_label
    s.period_start, s.period_end, s.card_label, s.period_confirmed = start, end, card_label, True
    if label_changed:
        reclassify(db_session, [g.user])
    db_session.commit()
    flash("集計期間を保存しました。対象金額は新しい期間で再計算されます。", "info")
    return redirect(url_for("main.settings"))


@bp.post("/settings/reset")
@login_required
def reset_data():
    """自分の明細(CSV取込・手動登録)を、全部または指定した月の分だけ削除する。家族の明細と設定は残す。"""
    if request.form.get("confirm") != "1":
        flash("「削除してよい」にチェックを入れてください。", "error")
        return redirect(url_for("main.settings"))
    query, month = delete(Transaction).where(Transaction.user_id == g.user.id), request.form.get("month")
    if month is not None:
        try:
            start = datetime.strptime(month, "%Y-%m")
        except ValueError:
            flash("削除する月を選んでください。", "error")
            return redirect(url_for("main.settings"))
        end = (start + timedelta(days=32)).replace(day=1)
        query = query.where(Transaction.used_at >= start, Transaction.used_at < end)
    count = db_session.execute(query).rowcount
    db_session.commit()
    flash(f"{f'{start:%Y年%m月}の' if month else 'すべての'}明細を{count}件削除しました。", "info")
    return redirect(url_for("main.settings"))


@bp.post("/rules")
@bp.post("/rules/<int:rule_id>/<action>")
@admin_required
def change_rule(rule_id=None, action="add"):
    """対象外ルールの追加・有効/無効の切り替え・削除。変えたら全員の取引を判定し直す。"""
    if action == "add":
        keyword = (request.form.get("keyword") or "").strip()[:100]
        if not keyword:
            flash("キーワードを入力してください。", "error")
            return redirect(url_for("main.settings"))
        db_session.add(ExclusionRule(keyword=keyword, memo=(request.form.get("memo") or "")[:200]))
        flash(f"対象外ルール「{keyword}」を追加し、全員の取引を再判定しました。", "info")
    else:
        rule = db_session.get(ExclusionRule, rule_id) or abort(404)
        if action == "toggle":
            rule.enabled = not rule.enabled
        elif action == "delete":
            db_session.delete(rule)
        else:
            abort(404)
    db_session.flush()
    reclassify(db_session)
    db_session.commit()
    return redirect(url_for("main.settings"))


@bp.app_template_filter("yen")
def yen(value) -> str:
    return "-" if value is None else f"{int(value):,}円"
