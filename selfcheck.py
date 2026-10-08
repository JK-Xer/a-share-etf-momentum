"""引擎自检：用可手算对照的构造数据验证回测/指标/策略的数值正确性。

运行：python selfcheck.py
全部通过时打印 ALL CHECKS PASSED。修改 quant/ 内代码后建议重跑。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant import backtest as B
from quant import metrics as MT
from quant import signals as SG
from quant import strategies as S

NAMES = {"AAA": "测试A", "BBB": "测试B", "CCC": "测试C"}


def make_closes(a: list[float], b: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(a), freq="B")
    return pd.DataFrame({"AAA": a, "BBB": b}, index=idx)


def approx(x: float, y: float, tol: float = 1e-9) -> bool:
    return abs(x - y) <= tol * max(1.0, abs(y))


def check_metrics():
    # 252 个净值点、每日 +0.1%：年化 = 1.001^252 - 1
    nav = pd.Series(np.cumprod(np.full(252, 1.001)),
                    index=pd.date_range("2024-01-01", periods=252, freq="B"))
    m = MT.compute_metrics(nav)
    assert approx(m["年化收益"], 1.001 ** 252 - 1), m["年化收益"]
    assert m["日胜率"] == 1.0
    assert m["最大回撤"] == 0.0

    # 已知回撤：净值 [1.0, 1.2, 0.9, 1.0] → 最大回撤 25%，峰=1.2 谷=0.9
    nav2 = pd.Series([1.0, 1.2, 0.9, 1.0],
                     index=pd.date_range("2024-01-01", periods=4, freq="B"))
    m2 = MT.compute_metrics(nav2)
    assert approx(m2["最大回撤"], -0.25), m2["最大回撤"]
    assert m2["最大回撤区间"][0] == nav2.index[1] and m2["最大回撤区间"][1] == nav2.index[2]

    # 分年度：两年各 +10%
    idx = pd.to_datetime(["2024-01-01", "2024-12-31", "2025-01-02", "2025-12-31"])
    y = MT.yearly_returns(pd.Series([1.0, 1.1, 1.1, 1.21], index=idx))
    assert approx(y.iloc[0], 0.10) and approx(y.iloc[1], 0.10), y
    print("[ok] metrics")


def check_engine_switch():
    """场景A：t0 满仓A，t1 收盘换成满仓B（单边成本率 0.001）。

    手算：t0 成本 1.0×0.001 → 0.999；t1 吃到 A +10% → ×1.10；t1 收盘换仓
    tau=2.0 → ×0.998；t2 吃到 B +5% → ×1.05；t3 B 走平。
    终值 = 0.999×1.10×0.998×1.05 = 1.15153731
    """
    closes = make_closes([100, 110, 110, 121], [100, 100, 105, 105])
    w = pd.DataFrame({"AAA": [1.0, np.nan], "BBB": [np.nan, 1.0]},
                     index=pd.DatetimeIndex([closes.index[0], closes.index[1]]))
    res = B.run_backtest(w, w.index, closes, NAMES,
                         commission=0.0005, slippage=0.0005,
                         benchmark="AAA", rf=0.0)
    expect = 0.999 * 1.10 * (1 - 2.0 * 0.001) * 1.05
    assert approx(res.nav.iloc[-1], expect, 1e-12), (res.nav.iloc[-1], expect)
    assert approx(res.turnover, 3.0, 1e-12), res.turnover
    assert len(res.trade_log) == 3
    assert res.benchmark_nav is not None and approx(res.benchmark_nav.iloc[-1], 1.21, 1e-9)
    print("[ok] engine switch scenario")


def check_engine_drift():
    """场景B：t0 五五开，t2 收盘再平衡回五五开。

    手算：t0 ×(1-0.001)；t1 A+10%×0.5 → ×1.05；t2 B+5%×0.5 → ×1.025，
    此时 A 漂移至 0.5/1.025，再平衡换手 tau = 2×(0.025/2.05)；
    t3 A+10%×0.5 → ×1.05。
    终值 = 0.999×1.05×1.025×(1-tau×0.001)×1.05
    """
    closes = make_closes([100, 110, 110, 121], [100, 100, 105, 105])
    w = pd.DataFrame({"AAA": [0.5, 0.5], "BBB": [0.5, 0.5]},
                     index=pd.DatetimeIndex([closes.index[0], closes.index[2]]))
    res = B.run_backtest(w, w.index, closes, NAMES,
                         commission=0.0005, slippage=0.0005,
                         benchmark="AAA", rf=0.0)
    tau = 2 * (0.025 / 2.05)
    expect = 0.999 * 1.05 * 1.025 * (1 - tau * 0.001) * 1.05
    assert approx(res.nav.iloc[-1], expect, 1e-12), (res.nav.iloc[-1], expect)
    print("[ok] engine drift scenario")


def check_engine_next_open():
    """场景C：次日开盘成交模式。

    收盘 A:[100,110,110,121]，开盘 A:[100,105,110,118]。
    t0 收盘信号买A → t1 开盘 105 成交（成本0.001）→ 日内 105→110；
    t2 持有不动（110→110）；t3 隔夜 110→118、日内 118→121。
    终值 = 0.999 × (110/105) × (118/110) × (121/118) = 0.999 × 121/105
    """
    idx = pd.date_range("2020-01-01", periods=4, freq="B")
    closes = pd.DataFrame({"AAA": [100, 110, 110, 121],
                           "BBB": [100, 100, 105, 105]}, index=idx)
    opens = pd.DataFrame({"AAA": [100, 105, 110, 118],
                          "BBB": [100, 100, 102, 105]}, index=idx)
    w = pd.DataFrame({"AAA": [1.0], "BBB": [np.nan]},
                     index=pd.DatetimeIndex([idx[0]]))
    res = B.run_backtest(w, w.index, closes, NAMES,
                         commission=0.0005, slippage=0.0005,
                         benchmark="AAA", rf=0.0,
                         opens=opens, execution="next_open")
    expect = 0.999 * 121 / 105
    assert approx(res.nav.iloc[-1], expect, 1e-12), (res.nav.iloc[-1], expect)
    # 成交日志应记在成交日(t1)，不是信号日(t0)
    assert res.trade_log["日期"].iloc[0] == idx[1]
    print("[ok] engine next-open scenario")


def check_engine_subset():
    """策略只涉及部分列（如二八轮动子池）：权重矩阵必须对齐到收盘价全列。

    t0 满仓A（成本 0.001），t1 收盘清仓（NaN 行=清仓，成本 0.001）：
    终值 = 0.999 × 1.10 × 0.999
    """
    closes = make_closes([100, 110, 110, 121], [100, 100, 105, 105])
    w = pd.DataFrame({"AAA": [1.0, np.nan]},
                     index=pd.DatetimeIndex([closes.index[0], closes.index[1]]))
    res = B.run_backtest(w, w.index, closes, NAMES,
                         commission=0.0005, slippage=0.0005,
                         benchmark="AAA", rf=0.0)
    expect = 0.999 * 1.10 * 0.999
    assert approx(res.nav.iloc[-1], expect, 1e-12), (res.nav.iloc[-1], expect)
    print("[ok] engine subset columns")


def check_momentum_subset_stop():
    """子池 + 止损：rank 序号与全矩阵列序号错位时止损仍读对列。

    构造：全矩阵 4 列 [CCC, AAA, BBB, DDD]，排序池取其中 3 列且顺序打乱。
    让池内某标的（rank 序号 0，全矩阵序号 2）先涨后崩，另一无关标的
    （rank 序号 1，全矩阵序号 0，恰为两者交换）同时暴跌——若止损误读
    到交换列，触发日期/行为会不同。验证：止损在真实持有标的崩盘日触发。
    """
    n = 100
    t = np.arange(n, dtype=float)
    px_a = 100 * 1.01 ** t                      # 稳涨（会被选中）
    px_a[70:82] = px_a[69] * np.linspace(1.0, 0.80, 12)  # 12 天急跌 20%
    px_a[82:] = px_a[81]                        # 之后横盘
    px_b = np.full(n, 50.0)
    px_b[50:] = 50 * 0.5 ** np.arange(n - 50)   # 无关标的腰斩（诱饵）
    px_c = 100 * 0.999 ** t
    px_d = 100 * np.ones(n)
    closes = pd.DataFrame({"AAA": px_a, "BBB": px_b, "CCC": px_c, "DDD": px_d},
                          index=pd.date_range("2021-01-01", periods=n, freq="B"))
    sub_pool = ["BBB", "AAA", "DDD"]  # rank 序号 0/1/2 ↔ 全矩阵序号 1/0/3 错位
    w, td = S.momentum_rotation(closes, lookback=20, top=1, rebalance=5,
                                min_momentum=0.0, stop_pct=0.10, pool=sub_pool)
    # 持有的始终应是 AAA（动量最高）；清仓/止损行表现为 AAA 为 NaN
    assert set(w.columns) <= {"AAA"}
    stop_rows = w.index[w["AAA"].isna()] if "AAA" in w.columns else w.index
    # 止损应在第 ~76 天触发（回撤 10%），明显早于动量翻负引发的调仓（~87 天）
    assert len(stop_rows) >= 1 and stop_rows[0] == closes.index[76], stop_rows[:3]
    print("[ok] momentum subset-pool trailing stop")


def check_momentum():
    """C 单边上涨、A 阴跌、B 走平：top=1 动量轮动应始终持有 C。"""
    n = 100
    t = np.arange(n, dtype=float)
    closes = pd.DataFrame({
        "AAA": 100 * 0.999 ** t,
        "BBB": 100.0,
        "CCC": 100 * 1.01 ** t,
    }, index=pd.date_range("2021-01-01", periods=n, freq="B"))
    w, td = S.momentum_rotation(closes, lookback=20, top=1, rebalance=5)
    # 目标持仓不变时去重：CCC 始终最强 → 只在首个决策日记一次调仓
    assert len(td) >= 1 and td[0] == closes.index[20]
    assert set(w.columns) == {"CCC"} and np.allclose(w["CCC"].values, 1.0)

    # 止损触发：持有 CCC 后某日较持有期高点回撤超 10% → 切入 BOND
    px = 100 * 1.01 ** t
    px[40:60] = px[39] * np.linspace(1.0, 0.85, 20)  # 缓跌 15%
    px[60:] = px[59] * 1.005 ** np.arange(n - 60)    # 企稳回升
    closes_stop = pd.DataFrame({"AAA": 100 * 0.999 ** t, "BBB": 100.0,
                                "CCC": px, "BOND": 100 * 1.001 ** t},
                               index=closes.index)
    w4, td4 = S.momentum_rotation(closes_stop, lookback=20, top=1, rebalance=5,
                                  fallback="BOND", abs_benchmark=True,
                                  stop_pct=0.10)
    rows4 = w4.dropna(how="all")
    assert "CCC" in rows4.columns and "BOND" in rows4.columns
    bond_days = rows4.index[rows4["CCC"].isna()] if "CCC" in rows4 else rows4.index
    assert len(bond_days) >= 1, "止损应至少触发一次"
    # 触发日应落在回撤段内（缓跌段中后段），而非回升段
    assert bond_days[0] >= closes.index[45], bond_days[0]

    # 全部动量为负 → 空仓（全 NaN 行；目标不变时去重为一次）
    closes_dn = pd.DataFrame({
        "AAA": 100 * 0.99 ** t, "BBB": 100 * 0.99 ** t, "CCC": 100 * 0.99 ** t,
    }, index=closes.index)
    w2, td2 = S.momentum_rotation(closes_dn, lookback=20, top=1, rebalance=5)
    assert len(td2) == 1 and w2.isna().all().all()

    # 全部权益动量为负 + 债券在池 → 债券动量最高而被选中
    closes_fb = closes_dn.copy()
    closes_fb["BOND"] = 100 * 1.001 ** t
    w3, _ = S.momentum_rotation(closes_fb, lookback=20, top=1, rebalance=5,
                                fallback="BOND")
    assert set(w3.columns) == {"BOND"} and np.allclose(w3["BOND"].values, 1.0)
    print("[ok] momentum strategy + trailing stop")


def check_dual_ma():
    """先走平、再上涨、再回落：fast=5/slow=20 应恰有两次翻转（进/出）。"""
    seg1 = np.full(40, 100.0)
    seg2 = np.linspace(100, 200, 40)
    seg3 = np.linspace(200, 100, 40)
    px = np.concatenate([seg1, seg2, seg3])
    closes = pd.DataFrame({"AAA": px},
                          index=pd.date_range("2022-01-01", periods=len(px), freq="B"))
    w, td = S.dual_ma_timing(closes, code="AAA", fast=5, slow=20, confirm_close=True)

    s = closes["AAA"]
    hold = (s.rolling(5).mean() > s.rolling(20).mean()) & (s > s.rolling(20).mean())
    hold = hold.fillna(False).astype(bool)
    change = hold != hold.shift(1, fill_value=False)
    expect_dates = list(s.index[change])
    assert list(td) == expect_dates, (td, expect_dates)
    assert len(td) == 2
    assert w.loc[td[0]].get("AAA") == 1.0 and pd.isna(w.loc[td[1]]).all()
    print("[ok] dual ma strategy")


def check_signals():
    holdings = {"AAA": 50000.0}
    df = SG.diff_holdings(holdings, cash=50000.0, target={"AAA": 1.0}, names=NAMES)
    row = df[df["代码"] == "AAA"].iloc[0]
    assert row["当前权重"] == 0.5 and row["目标权重"] == 1.0
    assert row["建议操作"] == "买入" and row["建议金额(¥)"] == 50000.0
    print("[ok] signals diff")


if __name__ == "__main__":
    check_metrics()
    check_engine_switch()
    check_engine_drift()
    check_engine_subset()
    check_engine_next_open()
    check_momentum()
    check_momentum_subset_stop()
    check_dual_ma()
    check_signals()
    print("ALL CHECKS PASSED")
