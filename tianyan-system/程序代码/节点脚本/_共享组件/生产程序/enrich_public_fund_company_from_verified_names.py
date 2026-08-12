from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "public_fund_company_name_enrichment_summary.json"
CN_TZ = timezone(timedelta(hours=8))

MANUAL_CODE_COMPANIES = {
    "001196": "东方基金",
    "002261": "中银基金",
    "002262": "中银基金",
    "002503": "中银基金",
    "003848": "中银基金",
    "003849": "中银基金",
    "003967": "中银基金",
    "004844": "中银基金",
    "016130": "国泰海通资管",
    "016131": "国泰海通资管",
    "017788": "摩根基金(中国)",
    "018351": "国泰海通资管",
    "018352": "国泰海通资管",
    "560000": "浦银安盛基金",
    "560650": "民生加银基金",
    "968048": "摩根基金(亚洲)有限公司",
    "968052": "摩根基金(亚洲)有限公司",
    "968130": "东亚联丰投资管理有限公司",
    "968157": "东亚联丰投资管理有限公司",
    "968163": "摩根基金(亚洲)有限公司",
}

OVERSEAS_FUND_CODES = {"968048", "968052", "968130", "968157", "968163"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill residual public-fund company values from verified share names and prefixes.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_name(value: Any) -> str:
    return re.sub(r"[（(]后端[)）]", "", str(value or "")).strip()


def ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute('PRAGMA table_info("基金标准分类字典")')}
    for column, column_type in [
        ("基金公司来源", "TEXT"),
        ("基金公司更新时间", "TEXT"),
        ("基金公司映射说明", "TEXT"),
    ]:
        if column not in columns:
            conn.execute(f'ALTER TABLE "基金标准分类字典" ADD COLUMN "{column}" {column_type}')
    conn.commit()


def main() -> int:
    args = parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    ensure_columns(conn)
    rows = [dict(row) for row in conn.execute('SELECT "基金代码", "标准基金名称", "基金公司" FROM "基金标准分类字典"')]
    company_ids = {
        row[0]: row[1]
        for row in conn.execute(
            '''
            SELECT "基金公司", "基金公司ID" FROM "公募基金公司映射采集"
            WHERE "采集状态"='成功' AND TRIM(COALESCE("基金公司", ''))<>''
            '''
        )
    }

    exact_names: dict[str, list[tuple[str, str]]] = defaultdict(list)
    prefix_companies: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        company = str(row["基金公司"] or "").strip()
        name = normalize_name(row["标准基金名称"])
        if not company or not name:
            continue
        exact_names[name].append((company, row["基金代码"]))
        for length in range(2, min(9, len(name) + 1)):
            prefix_companies[name[:length]][company] += 1

    updates: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for row in rows:
        code = row["基金代码"]
        name = normalize_name(row["标准基金名称"])
        current_company = str(row["基金公司"] or "").strip()
        manual_company = MANUAL_CODE_COMPANIES.get(code)
        if manual_company and current_company != manual_company:
            is_overseas = code in OVERSEAS_FUND_CODES
            updates.append(
                {
                    "code": code,
                    "name": row["标准基金名称"],
                    "company": manual_company,
                    "companyId": company_ids.get(manual_company, ""),
                    "source": "天天基金海外基金基本资料" if is_overseas else "基金名称及管理人品牌核验",
                    "note": (
                        f"海外基金基本资料页披露基金公司={manual_company}"
                        if is_overseas
                        else f"指定代码核验；基金名称品牌={manual_company}"
                    ),
                    "overwrite": bool(current_company),
                }
            )
            continue
        if current_company:
            continue

        exact = exact_names.get(name, [])
        exact_companies = {item[0] for item in exact}
        if len(exact_companies) == 1:
            company = next(iter(exact_companies))
            reference_code = sorted(item[1] for item in exact if item[0] == company)[0]
            updates.append(
                {
                    "code": code,
                    "name": row["标准基金名称"],
                    "company": company,
                    "companyId": company_ids.get(company, ""),
                    "source": "同名普通份额映射",
                    "note": f"去除后端标识后与已核验份额同名；参考代码={reference_code}",
                    "overwrite": False,
                }
            )
            continue

        candidates: list[tuple[int, str, int, str]] = []
        for length in range(2, min(9, len(name) + 1)):
            prefix = name[:length]
            counts = prefix_companies.get(prefix)
            if not counts:
                continue
            company, support = counts.most_common(1)[0]
            if support >= 2 and support == sum(counts.values()):
                candidates.append((length, company, support, prefix))
        if candidates:
            length, company, support, prefix = max(candidates, key=lambda item: item[0])
            updates.append(
                {
                    "code": code,
                    "name": row["标准基金名称"],
                    "company": company,
                    "companyId": company_ids.get(company, ""),
                    "source": "基金名称前缀_已验证同公司样本",
                    "note": f"唯一公司前缀={prefix}；已核验样本数={support}；前缀长度={length}",
                    "overwrite": False,
                }
            )
            continue

        unresolved.append(
            {
                "基金代码": code,
                "基金名称": row["标准基金名称"],
                "原因": "境内基金公司目录及已验证同名/前缀样本均未取得唯一公司",
            }
        )

    updated_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
    if not args.dry_run:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in updates:
                conn.execute(
                    '''
                    UPDATE "基金标准分类字典"
                    SET "基金公司"=?, "基金公司ID"=?, "基金公司来源"=?, "基金公司更新时间"=?, "基金公司映射说明"=?
                    WHERE "基金代码"=?
                      AND (TRIM(COALESCE("基金公司", ''))='' OR ?=1)
                    ''',
                    (
                        item["company"],
                        item["companyId"],
                        item["source"],
                        updated_at,
                        item["note"],
                        item["code"],
                        1 if item.get("overwrite") else 0,
                    ),
                )
            for item in unresolved:
                conn.execute(
                    '''
                    UPDATE "基金标准分类字典"
                    SET "基金公司映射说明"=?
                    WHERE "基金代码"=? AND TRIM(COALESCE("基金公司", ''))=''
                    ''',
                    (item["原因"], item["基金代码"]),
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    total, covered = conn.execute(
        '''
        SELECT COUNT(*), SUM(CASE WHEN TRIM(COALESCE("基金公司", ''))<>'' THEN 1 ELSE 0 END)
        FROM "基金标准分类字典"
        '''
    ).fetchone()
    source_counts = Counter(item["source"] for item in updates)
    summary = {
        "generatedAt": updated_at,
        "dryRun": bool(args.dry_run),
        "dbPath": str(args.db.resolve()),
        "candidateUpdates": len(updates),
        "sourceCounts": dict(source_counts),
        "companyCovered": covered,
        "companyMissing": total - covered,
        "coverageRate": round(covered / total, 6) if total else 0.0,
        "unresolved": unresolved,
        "updates": updates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    print(json.dumps({key: value for key, value in summary.items() if key != "updates"}, ensure_ascii=False, indent=2))
    print(f"summary={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
