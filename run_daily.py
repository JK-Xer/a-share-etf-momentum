"""命令行每日信号：更新数据并输出各预设策略的最新操作建议。

用法：
    python run_daily.py               # 更新数据 + 输出信号
    python run_daily.py --no-update   # 只用本地缓存输出信号

在 user_config.json 中保存过持仓时，同时输出与当前持仓的差额清单。
"""
from __future__ import annotations

import argparse

from config import BACKTEST_START, etf_pool, load_holdings
from quant import data as D
from quant import signals as SG
from quant import strategies as ST


def main() -> None:
    ap = argparse.ArgumentParser(description="A股 ETF 量化工具 - 每日信号")
    ap.add_argument("--no-update", action="store_true", help="跳过数据更新")
    args = ap.parse_args()

    pool = etf_pool()
    codes = list(pool)
    if not codes:
        print("ETF 池为空，请先配置。")
        return

    if not args.no_update:
        print("更新行情数据（akshare / 东方财富）…")
        D.update_cache(codes, BACKTEST_START,
                       on_status=lambda c, s: print(f"  {c} {pool.get(c, '')}: {s}"))
        print()

    closes = D.build_close_matrix(codes)
    print(f"数据截至 {closes.index[-1]:%Y-%m-%d}，共 {len(closes)} 个交易日\n")

    saved = load_holdings()
    for p in ST.PRESETS:
        spec = {"label": p["label"], **{k: v for k, v in p.items() if k != "label"}}
        sig = SG.latest_signal(spec, closes, pool)
        print(f"=== {sig['label']} | {sig['action']} ===")
        if sig["detail"]:
            print(f"    {sig['detail']}")
        if sig["target"]:
            for code, w in sorted(sig["target"].items()):
                print(f"    {code}  {pool.get(code, code):<10} {w:>6.1%}")
        else:
            print("    （空仓持币）")
        hh = saved.get("holdings", {})
        if hh or saved.get("cash"):
            diff = SG.diff_holdings(hh, saved.get("cash", 0.0), sig["target"], pool)
            if not diff.empty:
                print("    操作清单（与当前持仓差额，±0.5% 以内不动）：")
                print(diff.to_string(index=False))
        print()

    print("⚠️ 免责声明：信号由历史回测策略生成，不构成投资建议；回测收益不代表未来表现。")


if __name__ == "__main__":
    main()
