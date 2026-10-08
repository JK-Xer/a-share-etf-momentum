"""每日信号：用最新数据生成各策略今日的目标持仓与操作建议。"""
from __future__ import annotations

import pandas as pd

from quant import strategies as S


def latest_signal(
    spec: dict, closes: pd.DataFrame, names: dict[str, str] | None = None
) -> dict:
    """计算单个策略的最新信号。

    返回: {label, asof, action, detail, target: {代码: 权重}, trade_count}
    - action: 今日调仓 / 持有不动 / 数据不足
    - target: 当前生效（或今日调仓后）的目标持仓权重
    """
    names = names or {}
    label = spec.get("label", spec.get("type", ""))
    asof = closes.index[-1]
    weights, trade_days = S.run_strategy(spec, closes)
    if weights.empty or len(trade_days) == 0:
        return {
            "label": label, "asof": asof, "action": "数据不足",
            "detail": "回看期内没有可用行情，策略尚未产生信号",
            "target": {}, "trade_count": 0,
        }

    last_td = trade_days[-1]
    w = weights.iloc[-1].dropna()
    target = {c: float(v) for c, v in w.items()}

    detail = ""
    if last_td == asof:
        action = "今日调仓"
    else:
        action = "持有不动"
        detail = f"上次调仓 {last_td:%Y-%m-%d}，当前目标持仓继续生效"

    # 动量类策略给出距下一决策日的交易日数
    if spec.get("type") == "momentum":
        p = spec.get("params", {})
        sched = S.momentum_schedule(
            closes, p.get("lookback", 60), p.get("rebalance", 5)
        )
        if sched is not None:
            is_today, days_ahead = sched
            if is_today:
                if last_td == asof:
                    action = "今日调仓（决策日）"
                else:
                    action = "今日决策日，无需调仓"
                    detail = "决策日检查完成：目标持仓未变化，继续持有"
            else:
                detail = f"下次决策日约 {days_ahead} 个交易日后；当前目标持仓继续生效"

    return {
        "label": label, "asof": asof, "action": action,
        "detail": detail, "target": target,
        "trade_count": len(trade_days),
    }


def diff_holdings(
    holdings: dict[str, float],
    cash: float,
    target: dict[str, float],
    names: dict[str, str] | None = None,
) -> pd.DataFrame:
    """当前持仓(代码→市值) + 现金 与目标权重的差异 → 建议操作清单。

    权重差异小于 0.5% 视为无需操作。
    """
    names = names or {}
    total = float(sum(holdings.values())) + float(cash)
    if total <= 0:
        return pd.DataFrame()
    rows = []
    for code in sorted(set(holdings) | set(target)):
        cw = float(holdings.get(code, 0.0)) / total
        tw = float(target.get(code, 0.0))
        delta = (tw - cw) * total
        if abs(delta) < total * 0.005:
            op, amt = "持有", 0.0
        elif delta > 0:
            op, amt = "买入", delta
        else:
            op, amt = "卖出", -delta
        rows.append({
            "代码": code,
            "名称": names.get(code, code),
            "当前权重": cw,
            "目标权重": tw,
            "建议操作": op,
            "建议金额(¥)": round(amt, 0),
        })
    return pd.DataFrame(rows)
