# -*- coding: utf-8 -*-
"""组织层级一级欠进度为什么答不了（#32 取证，只读）。

用法：PYTHONIOENCODING=utf-8 python scripts/probe_plan_org_bridge.py

结论写在打印输出里：计划集的实际行只有 15 位定位单元编码，组织层级一要按编码前缀聚合，
而"按名称找组织层级一"会漏站、会撞重名，两条路的数不一样——这就是 #32 要拍的口径。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "engine.db"
PERIOD = "202608"
HAIKOU_PREFIX = "100047"


def main() -> None:
    con = sqlite3.connect(DB)

    def q(sql: str) -> list:
        return list(con.execute(sql))

    print(f"== 期间 {PERIOD} ==\n")
    print("计划 unitcode 长度分布（全表）:",
          q("select length(unitcode), count(*) from fact_oil_sales_plan group by 1 order by 1"))
    print("计划 unitcode 长度分布（本期）:",
          q(f"select length(unitcode), count(*), count(distinct unitcode) "
            f"from fact_oil_sales_plan where yyyymm='{PERIOD}' group by 1 order by 1"))
    print("实际 组织层级一编码-名称:",
          q(f"select bigareacode, bigareaname, round(sum(monthlysales),2) "
            f"from fact_oil_monthly_sales where yyyymm='{PERIOD}' "
            f"group by 1,2 order by 3 desc"))
    print("计划 按前 6 位聚合（应等于实际侧同编码的组织层级一）:",
          q(f"select substr(unitcode,1,6), count(distinct unitcode), round(sum(monsalesplan),2) "
            f"from fact_oil_sales_plan where yyyymm='{PERIOD}' group by 1 order by 3 desc"))
    print("计划本期合计:",
          q(f"select round(sum(monsalesplan),2) from fact_oil_sales_plan "
            f"where yyyymm='{PERIOD}'"))
    print("\n== 「示例区域甲」：名称桥 vs 编码桥 ==")
    print("编码桥 substr(unitcode,1,6)=100047:",
          q(f"select count(distinct unitcode), round(sum(monsalesplan),2) from fact_oil_sales_plan "
            f"where yyyymm='{PERIOD}' and substr(unitcode,1,6)='{HAIKOU_PREFIX}'"))
    print("名称桥 unitname like '示例区域甲%':",
          q(f"select count(distinct unitcode), round(sum(monsalesplan),2) from fact_oil_sales_plan "
            f"where yyyymm='{PERIOD}' and unitname like '示例区域甲%'"))
    print("编码桥里名称不以「示例区域甲」开头的定位单元（名称桥会漏掉它们）:",
          q(f"select unitcode, unitname from fact_oil_sales_plan where yyyymm='{PERIOD}' "
            f"and substr(unitcode,1,6)='{HAIKOU_PREFIX}' and unitname not like '示例区域甲%' "
            f"group by 1,2 limit 12"))
    print("\n== 重名风险（同名的两棵编码树，历史期间） ==")
    print(q("select unitcode, unitname, count(*), min(yyyymm), max(yyyymm) "
            "from fact_oil_sales_plan where unitname='示例区域甲示例行业组织层级一' group by 1,2"))
    print("实际侧 202608 无计划对应的组织层级一（完成率应显式缺失而不是 0）:",
          q(f"select bigareacode, bigareaname, round(sum(monthlysales),2) "
            f"from fact_oil_monthly_sales where yyyymm='{PERIOD}' "
            f"and (bigareacode is null or bigareacode not in "
            f"(select distinct substr(unitcode,1,6) from fact_oil_sales_plan where yyyymm='{PERIOD}')) "
            f"group by 1,2 order by 3 desc"))
    con.close()


if __name__ == "__main__":
    main()
