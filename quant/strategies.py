"""内置策略。

统一接口：输入收盘价矩阵 closes（后复权），输出 (weights, trade_days)：
- weights: DataFrame，index=调仓日，columns=代码，值为当日收盘后的目标权重；
  全 NaN 行（空 dict 行）表示「清仓持币」，同样是一次调仓；
- trade_days: 调仓日 DatetimeIndex，与 weights.index 一致。

调仓日网格从回测起点向后锚定（lookback 之后的每第 rebalance 个交易日），
规则完全确定，回测与实盘信号行为一致；界面上修改回测起点会平移调仓网格。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _weights_from_rows(rows: dict) -> pd.DataFrame:
    """rows: {调仓日: {代码: 权重}}，空 dict 表示清仓持币。

    必须保留空行（全 NaN）：pd.DataFrame.from_dict 会丢弃空 dict 行，
    而「清仓」本身是一次调仓、也是信号的一部分。
    """
    if not rows:
        return pd.DataFrame()
    cols = sorted({c for w in rows.values() for c in w})
    weights = pd.DataFrame(
        np.nan, index=pd.DatetimeIndex(list(rows.keys())), columns=cols
    )
    for d, w in rows.items():
        for c, v in w.items():
            weights.loc[d, c] = v
    weights.index.name = "date"
    return weights


def momentum_rotation(
    closes: pd.DataFrame,
    lookback: int = 60,
    top: int = 2,
    rebalance: int = 5,
    min_momentum: float = 0.0,
    fallback: str | None = None,
    abs_benchmark: bool = False,
    stop_pct: float | None = None,
    skip: int = 0,
    pool: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """ETF 动量轮动。

    每 rebalance 个交易日：在 pool（默认全部列）中取过去 lookback 日
    涨幅最高的 top 只等权持有；动量高于 min_momentum 的不足 top 只时
    只持有达标者；一只都不达标时持有 fallback（如国债 ETF）或空仓。

    abs_benchmark=True 时为双动量（Antonacci GEM 思路）：入选门槛从
    固定 0 提高为「动量须高于 fallback 自身的动量」。

    stop_pct=0.08 时启用持仓动态止损：两次调仓之间，某持仓收盘价较
    持有期内最高收盘回撤超过 stop_pct，立即切到 fallback/空仓，直至
    下一次常规调仓日重新选标。只用当日及之前的数据，无未来函数。

    skip>0 时跳过最近 skip 个交易日再算动量（经典 12-1 动量的做法，
    规避 A 股短期反转对排序的干扰）。
    """
    rank_cols = list(pool) if pool else list(closes.columns)
    rank_cols = [c for c in rank_cols if c in closes.columns]
    sub = closes[rank_cols]
    # 全程 numpy：逐日 pandas 标量访问（.at/.iloc/nlargest）比 numpy 慢百倍，
    # 参数扫描时这里是主要瓶颈
    mom_np = (sub.shift(skip) / sub.shift(lookback + skip) - 1.0).to_numpy()

    col_idx = {c: i for i, c in enumerate(closes.columns)}
    fb_idx: int | None = None
    fb_mom_np = None
    if fallback is not None and fallback in closes.columns:
        fb_idx = col_idx[fallback]
        fb = closes[fallback]
        fb_mom_np = (fb.shift(skip) / fb.shift(lookback + skip) - 1.0).to_numpy()
    elif fallback is not None:
        fallback = None  # 避险标的缺失时退化为空仓

    closes_np = closes.to_numpy(dtype=float)
    dates = closes.index
    n = len(dates)
    rows: dict = {}
    held: list[str] = []           # 当前持仓（不含 fallback）
    peaked: dict[int, float] = {}  # 持有期内最高收盘，键=列序号
    stopped = False                # 是否处于止损避险状态
    last_target: dict | None = None

    def defensive_row(i: int) -> dict:
        if fb_idx is not None and np.isfinite(closes_np[i, fb_idx]):
            return {fallback: 1.0}
        return {}

    for i in range(lookback, n):
        target: dict | None = None

        if (i - lookback) % rebalance == 0:
            row = mom_np[i]
            if not np.isfinite(row).any():
                continue  # 无可评估标的（都未上市），不产生调仓
            threshold = min_momentum
            if abs_benchmark and fb_mom_np is not None:
                fb_mom = fb_mom_np[i]
                if np.isfinite(fb_mom):
                    threshold = max(threshold, float(fb_mom))
            masked = np.where(np.isfinite(row), row, -np.inf)
            order = np.argsort(-masked, kind="stable")  # 降序，平值按列序
            sel: list[int] = []
            for j in order:
                if masked[j] <= threshold or len(sel) >= top:
                    break
                sel.append(int(j))
            if not sel:
                target = defensive_row(i)
                held, peaked, stopped = [], {}, True
            else:
                sel_names = [rank_cols[j] for j in sel]
                target = {c: 1.0 / len(sel) for c in sel_names}
                new_peaked: dict[int, float] = {}
                for c in sel_names:
                    jf = col_idx[c]  # 统一用全矩阵列序号，子池时 rank 序号会错位
                    px = closes_np[i, jf]
                    if not np.isfinite(px):
                        continue
                    if not stopped and c in held and jf in peaked:
                        # 延续持仓：保留持有期历史高点
                        new_peaked[jf] = max(peaked[jf], float(px))
                    else:
                        new_peaked[jf] = float(px)  # 新进入：重新起算
                held, peaked, stopped = sel_names, new_peaked, False
        elif stop_pct is not None and held and not stopped:
            # 持仓期内检查动态止损：收盘较持有期最高回撤超阈值
            for c in held:
                j = col_idx[c]
                px = closes_np[i, j]
                if np.isfinite(px):
                    peaked[j] = max(peaked.get(j, float(px)), float(px))
            if any(
                closes_np[i, j] / p - 1.0 < -stop_pct
                for j, p in peaked.items() if p > 0
            ):
                target = defensive_row(i)
                stopped = True

        if target is None or target == last_target:
            continue  # 无调仓，或目标持仓未变化
        rows[dates[i]] = target
        last_target = target

    weights = _weights_from_rows(rows)
    trade_days = pd.DatetimeIndex(list(rows.keys()))
    return weights, trade_days


def momentum_schedule(
    closes: pd.DataFrame, lookback: int, rebalance: int
) -> tuple[bool, int] | None:
    """动量轮动的决策日程：(今日是否决策日, 距下一决策日的交易日数)。"""
    n = len(closes.index)
    if n <= lookback:
        return None
    offset = (n - 1 - lookback) % rebalance
    is_today = offset == 0
    days_ahead = 0 if is_today else rebalance - offset
    return is_today, days_ahead


def dual_ma_timing(
    closes: pd.DataFrame,
    code: str = "510300",
    fast: int = 20,
    slow: int = 60,
    confirm_close: bool = True,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """双均线择时：快线在慢线上方（可选收盘价确认）时满仓持有，否则清仓。

    仅在持仓状态翻转的交易日调仓，状态不变时持仓随行情漂移。
    """
    if code not in closes.columns:
        raise ValueError(f"标的 {code} 不在行情数据中")
    s = closes[code].dropna().astype(float)
    f = s.rolling(fast).mean()
    sl = s.rolling(slow).mean()
    hold = (f > sl) & (s > sl) if confirm_close else (f > sl)
    hold = hold.fillna(False).astype(bool)
    change = hold != hold.shift(1, fill_value=False)

    rows: dict = {}
    for date, h in hold[change].items():
        rows[date] = {code: 1.0} if h else {}
    weights = _weights_from_rows(rows)
    trade_days = pd.DatetimeIndex(list(rows.keys()))
    return weights, trade_days


# ---- 策略规格与预设 ------------------------------------------------------

def run_strategy(spec: dict, closes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """按策略规格运行。spec: {type, params, pool(可选), label(可选)}。"""
    stype = spec.get("type")
    params = spec.get("params", {})
    if stype == "momentum":
        pool = spec.get("pool")
        return momentum_rotation(closes, pool=pool, **params)
    if stype == "dual_ma":
        return dual_ma_timing(closes, **params)
    raise ValueError(f"未知策略类型: {stype}")


# 预设策略集：信号页与策略对比页的默认选项。
# 池为「宽基+黄金+国债+跨境」12 只（剔除行业 ETF——行业动量在 A 股反转剧烈），
# 双动量 + 持仓止损。参数为 2015-2026 全样本扫描中的稳健区域，
# 成本假设见 config.COMMISSION 注释；回测收益不代表未来。
_PRESET_POOL = ["510300", "510500", "159915", "512100", "588000",
                "510880", "518880", "511010",
                "513100", "513500", "513050", "513130"]

PRESETS: list[dict] = [
    {
        "label": "多资产动量·稳健(T3+止损10%)",
        "type": "momentum",
        "pool": _PRESET_POOL,
        "params": {"lookback": 20, "top": 3, "rebalance": 5,
                   "fallback": "511010", "abs_benchmark": True, "stop_pct": 0.10},
    },
    {
        "label": "多资产动量·集中(T2+止损15%)",
        "type": "momentum",
        "pool": _PRESET_POOL,
        "params": {"lookback": 20, "top": 2, "rebalance": 5,
                   "fallback": "511010", "abs_benchmark": True, "stop_pct": 0.15},
    },
    {
        "label": "二八轮动·股债切换",
        "type": "momentum",
        "pool": ["510300", "510500"],
        "params": {"lookback": 20, "top": 1, "rebalance": 1, "fallback": "511010"},
    },
    {
        "label": "双均线·沪深300(20/60)",
        "type": "dual_ma",
        "params": {"code": "510300", "fast": 20, "slow": 60},
    },
]
