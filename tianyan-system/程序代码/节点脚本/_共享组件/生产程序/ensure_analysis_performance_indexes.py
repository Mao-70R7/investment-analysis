from __future__ import annotations

import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"

INDEX_SQL = [
    'CREATE INDEX IF NOT EXISTS "idx_策略调仓明细_事件_基金" ON "策略调仓明细"("调仓事件ID", "基金代码")',
    'CREATE INDEX IF NOT EXISTS "idx_策略模拟净值区间_算法_事件" ON "策略模拟净值区间"("算法版本", "渠道ID", "是否纳入模拟", "区间是否有效", "调仓事件ID")',
    'CREATE INDEX IF NOT EXISTS "idx_策略模拟净值_算法" ON "策略模拟净值"("算法版本")',
    'CREATE INDEX IF NOT EXISTS "idx_策略模拟净值质量_算法" ON "策略模拟净值质量"("算法版本", "渠道ID")',
    'CREATE INDEX IF NOT EXISTS "idx_策略官方偏差分析_算法" ON "策略官方偏差分析"("算法版本", "渠道ID")',
    'CREATE INDEX IF NOT EXISTS "idx_基金分红送配_基金_分红日" ON "基金分红送配"("基金代码", "除息日", "权益登记日")',
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        for sql in INDEX_SQL:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"数据库": str(DB_PATH.resolve()), "索引数": len(INDEX_SQL)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
