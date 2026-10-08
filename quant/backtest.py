"""回测引擎。

执行约定（杜绝未来函数）：
- 策略在 T 日收盘后给出目标权重，按 T 日收盘价调仓；
- T 日的涨跌归属调仓前的旧持仓（即信号不使用 T 日之后的任何数据）；
- 两次调仓之间持仓不动，权重随行情漂移，不做每日再平衡；
- 调仓成本按单边换手 × (佣金+滑点) 收取，直接扣减净值。

接口约定：策略输出 (weights, trade_days)——weights 仅在 trade_days 上有
目标权重行；全 NaN 的行表示「清仓持币」，也是一次调仓。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import BENCHMARK, COMMISSION, RISK_FREE, SLIPPAGE
from quant import metrics as MT
from quant import strategies as ST


def run_backtest_from_spec(
    spec: dict,
    closes: pd.DataFrame,
    etf_names: dict[str, str] | None = None,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
    benchmark: str = BENCHMARK,
    rf: float = RISK_FREE,
    opens: pd.DataFrame | None = None,
    execution: str = "close",
) -> BacktestResult:
    """便捷封装：策略规格 {type, params, pool} → 生成信号 → 回测。"""
    weights, trade_days = ST.run_strategy(spec, closes)
    return run_backtest(weights, trade_days, closes, etf_names,
                        commission=commission, slippage=slippage,
                        benchmark=benchmark, rf=rf,
                        opens=opens, execution=execution)


@dataclass
class BacktestResult:
    nav: pd.Series                      # 策略净值（期初=1）
    benchmark_nav: pd.Series | None     # 基准净值（买入持有）
    trade_log: pd.DataFrame             # 调仓明细
    turnover: float                     # 累计单边换手
    metrics: dict
    benchmark_metrics: dict | None
    yearly: pd.Series                   # 策略分年度收益
    benchmark_yearly: pd.Series | None  # 基准分年度收益


def run_backtest(
    weights: pd.DataFrame,
    trade_days: pd.DatetimeIndex,
    closes: pd.DataFrame,
    etf_names: dict[str, str] | None = None,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
    benchmark: str = BENCHMARK,
    rf: float = RISK_FREE,
    opens: pd.DataFrame | None = None,
    execution: str = "close",
) -> BacktestResult:
    """按执行约定运行回测。

    weights: index=调仓日(不必连续)，columns=ETF 代码；仅在 trade_days 上有行。
    trade_days: 明确的调仓日序列（weights.index 与其一致）。
    closes: 收盘价矩阵（后复权），其 index 决定回测日历，列必须覆盖持仓代码。

    execution="close"（默认）：信号日收盘产生目标权重并按当日收盘价成交。
    execution="next_open"：信号日收盘产生信号，次日开盘价成交——更贴近
    「收盘后看信号、次日开盘下单」的实盘流程；需要提供 opens 矩阵。
    """
    etf_names = etf_names or {}
    cost_rate = commission + slippage
    dates = closes.index
    cols = list(closes.columns)
    next_open = execution == "next_open"
    if next_open and opens is None:
        raise ValueError("execution='next_open' 需要提供 opens 开盘价矩阵")

    C = closes.to_numpy(dtype=float)
    O = opens.reindex(index=dates, columns=cols).to_numpy(dtype=float) if next_open else None

    # 收益分解：隔夜(昨收→今开) 与 日内(今开→今收)；未上市/首日 → 0
    if next_open:
        prevC = np.roll(C, 1, axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            r_on = np.where((O > 0) & (C > 0) & (prevC > 0), O / prevC - 1.0, 0.0)
            r_in = np.where((O > 0) & (C > 0), C / O - 1.0, 0.0)
        r_on[0] = 0.0  # 首日无昨收
    else:
        prevC = np.roll(C, 1, axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            R = np.where((C > 0) & (prevC > 0), C / prevC - 1.0, 0.0)
        R[0] = 0.0

    Wfull = weights.reindex(index=dates, columns=cols)  # 对齐到全部列，缺失记 0
    valid_days = pd.DatetimeIndex([d for d in trade_days if d in dates])
    # 信号日 → 成交日映射：close 模式当日成交；next_open 次日开盘成交
    if next_open:
        sig_mask = np.asarray(dates.isin(valid_days), dtype=bool)
        trade_flag = np.zeros(len(dates), dtype=bool)  # trade_flag[t]=t 日开盘成交
        trade_flag[1:] = sig_mask[:-1]
    else:
        trade_flag = np.asarray(dates.isin(valid_days), dtype=bool)
    W = np.nan_to_num(Wfull.to_numpy(dtype=float), nan=0.0)
    # 防御：权重非负、合计不超过 1（超配部分等比压缩）
    W = np.clip(W, 0.0, None)
    over = W.sum(axis=1, keepdims=True)
    W = W / np.where(over > 1.0, over, 1.0)  # 超配部分等比压缩（避免除零）

    col_pos = {c: i for i, c in enumerate(cols)}
    h = np.zeros(len(cols))
    nav = 1.0
    nav_arr = np.empty(len(dates))
    trades: list[dict] = []
    turnover_total = 0.0

    def make_trades(t: int, drifted: np.ndarray, w: np.ndarray) -> float:
        """记录成交明细，返回净值成本乘数（1.0 = 无需成交）。"""
        nonlocal turnover_total
        delta = w - drifted
        tau = float(np.abs(delta).sum())
        if tau <= 1e-6:
            return 1.0
        turnover_total += tau
        for c, i in col_pos.items():
            d = float(delta[i])
            if abs(d) > 5e-4:
                trades.append({
                    "日期": dates[t],
                    "代码": c,
                    "名称": etf_names.get(c, c),
                    "操作": "买入" if d > 0 else "卖出",
                    "变动权重": d,
                    "调整后权重": float(w[i]),
                })
        return 1.0 - tau * cost_rate

    for t in range(len(dates)):
        if next_open:
            # 隔夜：旧持仓拿昨收→今开
            g_on = 1.0 + float(h @ r_on[t])
            nav *= g_on
            traded = False
            if trade_flag[t]:
                drifted = h * (1.0 + r_on[t]) / g_on if g_on > 0 else np.zeros_like(h)
                w = W[t - 1]  # 信号产生于前一日收盘
                nav *= make_trades(t, drifted, w)
                h = w.copy()
                traded = True
            # 日内：当前持仓拿今开→今收
            g_in = 1.0 + float(h @ r_in[t])
            nav *= g_in
            # 权重漂移到收盘口径：成交日新持仓只经历日内段，未成交则两段都经历
            if traded:
                h = h * (1.0 + r_in[t]) / g_in if g_in > 0 else np.zeros_like(h)
            else:
                denom = g_on * g_in
                h = h * (1.0 + r_on[t]) * (1.0 + r_in[t]) / denom if denom > 0 \
                    else np.zeros_like(h)
        else:
            day_ret = float(h @ R[t])
            nav *= (1.0 + day_ret)
            if trade_flag[t]:
                drifted = h * (1.0 + R[t]) / (1.0 + day_ret) if (1.0 + day_ret) > 0 \
                    else np.zeros_like(h)
                w = W[t]
                nav *= make_trades(t, drifted, w)
                h = w.copy()
        nav_arr[t] = nav

    nav_series = pd.Series(nav_arr, index=dates, name="策略净值")
    metrics = MT.compute_metrics(
        nav_series, rf=rf, one_side_turnover=turnover_total
    )

    bench_nav: pd.Series | None = None
    bench_metrics: dict | None = None
    bench_yearly: pd.Series | None = None
    if benchmark in closes.columns:
        b = closes[benchmark].reindex(dates)
        first = b.first_valid_index()
        b = b.loc[first:]
        b = b / float(b.iloc[0])
        bench_nav = b
        bench_metrics = MT.compute_metrics(b, rf=rf)
        bench_yearly = MT.yearly_returns(b)

    trade_log = (
        pd.DataFrame(trades)
        if trades
        else pd.DataFrame(columns=["日期", "代码", "名称", "操作", "变动权重", "调整后权重"])
    )

    return BacktestResult(
        nav=nav_series,
        benchmark_nav=bench_nav,
        trade_log=trade_log,
        turnover=turnover_total,
        metrics=metrics,
        benchmark_metrics=bench_metrics,
        yearly=MT.yearly_returns(nav_series),
        benchmark_yearly=bench_yearly,
    )
