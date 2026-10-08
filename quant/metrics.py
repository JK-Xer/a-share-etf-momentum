"""绩效指标计算：年化、最大回撤、夏普、卡玛、胜率、换手、分年度收益。"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def compute_metrics(
    nav: pd.Series,
    rf: float = 0.0,
    one_side_turnover: float | None = None,
) -> dict:
    """由净值序列计算全部核心指标。nav 为等比净值（期初=1）。

    rf 用于夏普比率；one_side_turnover 为回测期间累计单边换手，
    提供时额外计算年化换手。
    """
    nav = nav.dropna().astype(float)
    if len(nav) < 2 or nav.iloc[0] <= 0:
        return {}

    ret = nav.pct_change().dropna()
    n = len(ret)
    years = n / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    ann_ret = (1.0 + total) ** (1.0 / years) - 1 if years > 0 else np.nan
    ann_vol = float(ret.std(ddof=1)) * np.sqrt(TRADING_DAYS)
    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else np.nan

    cummax = nav.cummax()
    dd = nav / cummax - 1.0
    max_dd = float(dd.min())
    if max_dd < 0:
        trough = dd.idxmin()
        peak = nav.loc[:trough].idxmax()
        after = nav.loc[trough:]
        recovered = after[after >= nav.loc[peak]]
        recovery = recovered.index[0] if len(recovered) else None
        dd_days = (trough - peak).days
    else:
        peak = trough = recovery = None
        dd_days = 0

    calmar = ann_ret / abs(max_dd) if max_dd < 0 else np.nan
    win_daily = float((ret > 0).mean())
    monthly = nav.resample("ME").last()
    mret = monthly.pct_change().dropna()
    win_monthly = float((mret > 0).mean()) if len(mret) else np.nan

    return {
        "总收益": total,
        "年化收益": ann_ret,
        "年化波动": ann_vol,
        "夏普": sharpe,
        "最大回撤": max_dd,
        "最大回撤区间": (peak, trough) if peak is not None else None,
        "回撤恢复日": recovery,
        "最大回撤天数": dd_days,
        "卡玛": calmar,
        "日胜率": win_daily,
        "月胜率": win_monthly,
        "交易日数": n,
        "年数": years,
        "年化单边换手": (
            one_side_turnover / years
            if one_side_turnover is not None and years > 0 else None
        ),
    }


def yearly_returns(nav: pd.Series) -> pd.Series:
    """分自然年度收益（首个不完整年份从回测起点算起）。"""
    nav = nav.dropna().astype(float)
    if len(nav) < 2:
        return pd.Series(dtype=float)
    out: dict[int, float] = {}
    prev = nav.iloc[0]
    for year, sub in nav.groupby(nav.index.year):
        out[int(year)] = sub.iloc[-1] / prev - 1.0
        prev = sub.iloc[-1]
    return pd.Series(out)


def drawdown_series(nav: pd.Series) -> pd.Series:
    """逐日回撤序列（相对历史高点）。"""
    nav = nav.dropna().astype(float)
    return nav / nav.cummax() - 1.0
