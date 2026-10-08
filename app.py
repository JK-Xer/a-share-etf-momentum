"""A股 ETF 量化工具 —— Streamlit Web 界面。

启动：python -m streamlit run app.py
页面：回测 / 策略对比 / 每日信号 / 数据管理。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config import (ANNUAL_TARGET, BACKTEST_START, BASE_DIR, BENCHMARK,
                    COMMISSION, RISK_FREE, SLIPPAGE, etf_pool, load_holdings,
                    save_etf_pool, save_holdings)
from quant import backtest as BT
from quant import data as D
from quant import metrics as MT
from quant import signals as SG
from quant import strategies as ST

st.set_page_config(page_title="A股 ETF 量化工具", page_icon="📈", layout="wide")

COLOR_STRATEGY = "#c0392b"
COLOR_BENCH = "#7f8c8d"

if "data_version" not in st.session_state:
    st.session_state.data_version = 0


# ---------- 通用 ----------

@st.cache_data(ttl=600, show_spinner=False)
def _load_closes(codes: tuple, version: int, start: str, column: str) -> pd.DataFrame:
    """读取本地缓存的价格矩阵（version 变化时失效重新加载）。"""
    return D.build_close_matrix(list(codes), start=start, column=column)


def get_closes() -> pd.DataFrame:
    pool = etf_pool()
    codes = tuple(pool.keys())
    if not codes:
        st.error("ETF 池为空，请到「数据管理」页添加。")
        st.stop()
    try:
        return _load_closes(codes, st.session_state.data_version, BACKTEST_START, "close")
    except RuntimeError as e:
        st.error(str(e))
        st.stop()


def get_opens() -> pd.DataFrame:
    pool = etf_pool()
    codes = tuple(pool.keys())
    return _load_closes(codes, st.session_state.data_version, BACKTEST_START, "open")


def gate_badge(ann: float) -> None:
    """年化收益 15% 门槛判定（仅代表历史回测结果）。"""
    if ann >= ANNUAL_TARGET:
        st.success(
            f"✅ **达标**：回测年化 {ann:.1%} ≥ 目标 {ANNUAL_TARGET:.0%}"
            "（仅为历史回测结果，不代表未来）"
        )
    else:
        st.error(f"❌ **未达标**：回测年化 {ann:.1%} < 目标 {ANNUAL_TARGET:.0%}")


def metrics_row(m: dict) -> None:
    cols = st.columns(6)
    cols[0].metric("年化收益", f"{m['年化收益']:.1%}")
    cols[1].metric("最大回撤", f"{m['最大回撤']:.1%}")
    cols[2].metric("夏普比率", f"{m['夏普']:.2f}")
    cols[3].metric("卡玛比率", f"{m['卡玛']:.2f}")
    cols[4].metric("月胜率", f"{m['月胜率']:.0%}")
    turn = m.get("年化单边换手")
    cols[5].metric("年化换手(单边)", f"{turn:.1f}x" if turn is not None else "—")


def chart_nav(res: BT.BacktestResult, show_trades: bool = False) -> None:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32],
        vertical_spacing=0.04, subplot_titles=("净值对比", "策略回撤"),
    )
    fig.add_trace(go.Scatter(x=res.nav.index, y=res.nav, name="策略",
                             line=dict(color=COLOR_STRATEGY, width=2)), row=1, col=1)
    if res.benchmark_nav is not None:
        fig.add_trace(go.Scatter(x=res.benchmark_nav.index, y=res.benchmark_nav,
                                 name="沪深300ETF 买入持有",
                                 line=dict(color=COLOR_BENCH, width=1.3)), row=1, col=1)
    if show_trades and not res.trade_log.empty:
        # 调仓日标记（净值曲线上的散点）
        tl = res.trade_log.drop_duplicates(subset="日期")
        marks = res.nav.reindex(tl["日期"]).dropna()
        fig.add_trace(go.Scatter(
            x=marks.index, y=marks.values, mode="markers", name="调仓日",
            marker=dict(symbol="diamond", size=6, color="#2c3e50", opacity=0.7),
            hovertemplate="%{x|%Y-%m-%d}<extra>调仓日</extra>"), row=1, col=1)
    dd = MT.drawdown_series(res.nav)
    fig.add_trace(go.Scatter(x=dd.index, y=dd, name="策略回撤", fill="tozeroy",
                             line=dict(color="#e67e22", width=1),
                             hovertemplate="%{y:.1%}<extra>回撤</extra>"), row=2, col=1)
    if res.benchmark_nav is not None:
        ddb = MT.drawdown_series(res.benchmark_nav)
        fig.add_trace(go.Scatter(x=ddb.index, y=ddb, name="基准回撤",
                                 line=dict(color=COLOR_BENCH, width=1)), row=2, col=1)
    fig.update_layout(height=600, hovermode="x unified",
                      legend=dict(orientation="h", y=1.06))
    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="回撤", tickformat=".0%", row=2, col=1)
    st.plotly_chart(fig, width="stretch")


def chart_yearly(res: BT.BacktestResult) -> None:
    fig = go.Figure()
    fig.add_bar(x=res.yearly.index.astype(str), y=res.yearly.values,
                name="策略", marker_color=COLOR_STRATEGY)
    if res.benchmark_yearly is not None:
        fig.add_bar(x=res.benchmark_yearly.index.astype(str),
                    y=res.benchmark_yearly.values, name="基准",
                    marker_color=COLOR_BENCH)
    fig.update_layout(barmode="group", height=340,
                      yaxis_tickformat=".0%", yaxis_title="年度收益",
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, width="stretch")


def chart_monthly_heatmap(res: BT.BacktestResult) -> None:
    """策略月度收益热力图（行=年，列=月），逐月表现一目了然。"""
    nav = res.nav
    mret = nav.resample("ME").last().pct_change()
    first = nav.resample("ME").last()
    mret.iloc[0] = first.iloc[0] / nav.iloc[0] - 1.0  # 首月从回测起点算
    df = pd.DataFrame({
        "年": mret.index.year, "月": mret.index.month, "收益": mret.values,
    }).pivot(index="年", columns="月", values="收益")
    df.columns = [f"{m}月" for m in df.columns]
    zmax = float(np.nanmax(np.abs(df.values))) or 0.05

    fig = go.Figure(go.Heatmap(
        z=df.values, x=list(df.columns), y=df.index.astype(str),
        zmin=-zmax, zmax=zmax, xgap=2, ygap=2,
        colorbar=dict(title="收益", tickformat=".0%"),
        colorscale="RdYlGn",
        # 预格式化文本：无数据月份显示空白而非 NaN%
        text=[[f"{v:.1%}" if pd.notna(v) else "" for v in row] for row in df.values],
        texttemplate="%{text}",
        hovertemplate="%{y}年%{x}: %{z:.1%}<extra></extra>",
    ))
    fig.update_layout(height=max(240, 28 * len(df) + 120), yaxis_autorange="reversed")
    st.plotly_chart(fig, width="stretch")


def chart_rolling_annual(res: BT.BacktestResult, window: int = 252) -> None:
    """滚动 1 年年化收益：检验策略是否存在「长期失效期」。"""
    roll = res.nav.rolling(window).apply(
        lambda x: float(x[-1] / x[0]) ** (252 / len(x)) - 1.0, raw=True
    )
    fig = go.Figure()
    fig.add_hline(y=ANNUAL_TARGET, line_dash="dot", line_color="#27ae60",
                  annotation_text=f"门槛 {ANNUAL_TARGET:.0%}",
                  annotation_position="top left")
    fig.add_hline(y=0.0, line_color="#95a5a6", line_width=1)
    fig.add_trace(go.Scatter(x=roll.index, y=roll.values, name="滚动1年年化",
                             line=dict(color=COLOR_STRATEGY, width=1.6),
                             hovertemplate="%{x|%Y-%m}: %{y:.1%}<extra>滚动年化</extra>"))
    fig.update_layout(height=300, yaxis_tickformat=".0%",
                      yaxis_title="滚动1年年化", showlegend=False)
    st.plotly_chart(fig, width="stretch")


# ---------- 页面 1：回测 ----------

def page_backtest() -> None:
    st.header("📊 策略回测")
    st.caption(
        "执行约定：信号日收盘产生目标权重，按所选成交价撮合；"
        "成本 = 单边换手 ×（佣金+滑点）。调仓日网格从回测起点锚定。"
    )
    names = etf_pool()
    closes = get_closes()

    with st.form("bt_form"):
        c1, c2 = st.columns([1.2, 1])
        with c1:
            stype = st.selectbox("策略类型", ["ETF 动量轮动", "双均线择时"])
            if stype == "ETF 动量轮动":
                sel_pool = st.multiselect("轮动池（不选=全部）", list(names),
                                          default=list(names),
                                          format_func=lambda c: f"{c} {names[c]}")
                k1, k2 = st.columns(2)
                lookback = k1.slider("动量回看期(交易日)", 10, 250, 20, 5)
                top = k2.slider("持有数量 top M", 1, 5, 3)
                k3, k4 = st.columns(2)
                rebalance = k3.slider("调仓间隔(交易日)", 1, 60, 5)
                skip = k4.slider("跳过近期天数", 0, 30, 0, 5,
                                 help="经典 12-1 动量：跳过最近几日再算动量，规避短期反转")
                k5, k6 = st.columns(2)
                stop_pct = k5.slider("持仓止损回撤", 0.0, 0.20, 0.10, 0.01,
                                     help="持有期内从最高收盘回撤超过该值立即切避险标的；0=不启用")
                min_mom = k6.slider("最低动量门槛", -0.20, 0.20, 0.0, 0.05,
                                    help="动量低于该值的 ETF 不入选")
                fb_opts = ["空仓"] + [f"{c} {names[c]}" for c in names]
                fb_sel = st.selectbox("弱市避险标的", fb_opts,
                                      index=fb_opts.index("511010 国债ETF")
                                      if "511010 国债ETF" in fb_opts else 0)
                abs_bench = st.checkbox(
                    "双动量（动量须跑赢避险标的才算达标）", value=True,
                    help="Antonacci GEM：权益动量连国债都跑不赢时持国债，而非死守正动量权益")
            else:
                code = st.selectbox("标的", list(names),
                                    format_func=lambda c: f"{c} {names[c]}")
                k1, k2 = st.columns(2)
                fast = k1.slider("快线(日)", 5, 120, 20, 5)
                slow = k2.slider("慢线(日)", 20, 250, 60, 5)
                confirm = st.checkbox("收盘价确认（需在慢线上方）", value=True)
        with c2:
            d1, d2 = st.columns(2)
            start = d1.date_input("开始日期", value=date(2015, 1, 1),
                                  min_value=date(2010, 1, 1),
                                  max_value=date.today())
            end = d2.date_input("结束日期", value=date.today(),
                                min_value=date(2010, 1, 1),
                                max_value=date.today())
            commission = st.slider("单边佣金", 0.0, 0.002, COMMISSION, 0.0001,
                                   format="%.4f")
            slippage = st.slider("单边滑点", 0.0, 0.003, SLIPPAGE, 0.0005,
                                 format="%.4f")
            execution = st.radio(
                "成交价假设", ["信号日收盘", "次日开盘"],
                help="「次日开盘」更贴近收盘后看信号、次日开盘下单的实盘流程，"
                     "结果通常比收盘成交略保守；「信号日收盘」为行业惯例口径")
        run = st.form_submit_button("▶ 运行回测", type="primary",
                                    width="stretch")

    if not run and "bt_result" not in st.session_state:
        st.info("设置参数后点击「运行回测」。")
        return
    if run:
        if end <= start:
            st.error("结束日期必须晚于开始日期。")
            return
        cs = closes.loc[pd.Timestamp(start):pd.Timestamp(end)]
        if stype == "ETF 动量轮动":
            fallback = None
            if fb_sel != "空仓":
                fallback = fb_sel.split(" ")[0]
            spec = {"label": stype, "type": "momentum",
                    "params": {"lookback": lookback, "top": top,
                               "rebalance": rebalance, "min_momentum": min_mom,
                               "skip": skip,
                               "stop_pct": stop_pct if stop_pct > 0 else None,
                               "abs_benchmark": abs_bench,
                               "fallback": fallback}}
            if sel_pool:
                spec["pool"] = sel_pool
        else:
            spec = {"label": stype, "type": "dual_ma",
                    "params": {"code": code, "fast": fast, "slow": slow,
                               "confirm_close": confirm}}
        with st.spinner("回测计算中…"):
            if execution == "次日开盘":
                opens = get_opens().loc[pd.Timestamp(start):pd.Timestamp(end)]
                res = BT.run_backtest_from_spec(
                    spec, cs, names, commission=commission, slippage=slippage,
                    opens=opens, execution="next_open")
            else:
                res = BT.run_backtest_from_spec(spec, cs, names,
                                                commission=commission,
                                                slippage=slippage)
        st.session_state.bt_result = res
        st.session_state.bt_spec = spec
        st.session_state.bt_exec = execution

    res: BT.BacktestResult = st.session_state.bt_result
    spec: dict = st.session_state.bt_spec

    m = res.metrics
    if not m:
        st.error("回测区间数据不足，请扩大日期范围。")
        return

    st.subheader(spec["label"])
    exec_tag = st.session_state.get("bt_exec", "信号日收盘")
    st.caption(f"成交口径：{exec_tag}")
    gate_badge(m["年化收益"])
    metrics_row(m)
    peak, trough = m["最大回撤区间"] or (None, None)
    rec = m["回撤恢复日"]
    if peak is not None:
        st.caption(
            f"最大回撤区间 {peak:%Y-%m-%d} → {trough:%Y-%m-%d}"
            + (f"，已于 {rec:%Y-%m-%d} 收复" if rec is not None else "，尚未收复")
            + f"（共 {m['最大回撤天数']} 个自然日）"
        )
    if res.benchmark_metrics:
        b = res.benchmark_metrics
        st.caption(
            f"同期基准（沪深300 买入持有）：年化 {b['年化收益']:.1%}，"
            f"最大回撤 {b['最大回撤']:.1%}，策略超额年化 "
            f"{m['年化收益'] - b['年化收益']:+.1%}"
        )

    show_trades = st.toggle("净值图上标记调仓日", value=False)
    chart_nav(res, show_trades=show_trades)

    st.subheader("分年度收益")
    chart_yearly(res)
    yearly = res.yearly.rename("策略").to_frame()
    if res.benchmark_yearly is not None:
        yearly = yearly.join(res.benchmark_yearly.rename("基准"))
    st.dataframe(yearly.style.format("{:.1%}"), height=260,
                 width="content")

    t1, t2 = st.tabs(["📅 月度收益热力图", "📉 滚动1年年化"])
    with t1:
        chart_monthly_heatmap(res)
    with t2:
        chart_rolling_annual(res)
        st.caption("滚动年化长期低于 0 的区间 = 策略失效期，请评估自己能否拿得住。")

    n_trades = len(res.trade_log)
    st.subheader(f"调仓明细（共 {n_trades} 笔，累计单边换手 {res.turnover:.1f} 倍）")
    dc1, dc2, _ = st.columns([1, 1, 3])
    if dc1.button("⬇ 导出调仓明细 CSV"):
        st.session_state.download_trades = res.trade_log.to_csv(index=False).encode("utf-8-sig")
    if dc1 and st.session_state.get("download_trades"):
        st.download_button("保存 调仓明细.csv", st.session_state.download_trades,
                           "调仓明细.csv", "text/csv")
    if dc2.button("⬇ 导出净值序列 CSV"):
        st.session_state.download_nav = res.nav.to_csv().encode("utf-8-sig")
    if dc2 and st.session_state.get("download_nav"):
        st.download_button("保存 净值序列.csv", st.session_state.download_nav,
                           "净值序列.csv", "text/csv")
    st.dataframe(res.trade_log, height=320, width="stretch")


# ---------- 页面 2：策略对比 ----------

def page_compare() -> None:
    st.header("⚖️ 策略对比")
    st.caption("批量回测多组策略/参数并排名（区间固定为数据起点至今）；"
               "支持样本内/外切分，检验参数是否过拟合。")
    names = etf_pool()
    closes = get_closes()

    with st.form("cmp_form"):
        labels = st.multiselect(
            "选择预设策略", [p["label"] for p in ST.PRESETS],
            default=[p["label"] for p in ST.PRESETS],
        )
        st.write("**自定义动量参数扫描**（可选，与预设一起参与对比）")
        g1, g2, g3 = st.columns(3)
        looks = g1.multiselect("回看期", [20, 40, 60, 90, 120, 180], default=[])
        tops = g2.multiselect("持有数量", [1, 2, 3], default=[])
        rebs = g3.multiselect("调仓间隔", [1, 5, 10, 20], default=[])
        use_split = st.checkbox("样本内/外切分（防过拟合检查）", value=True)
        split_date = st.date_input("切分日期", value=date(2021, 1, 1),
                                   min_value=date(2010, 1, 1),
                                   max_value=date.today())
        run = st.form_submit_button("▶ 批量回测", type="primary",
                                    width="stretch")

    if run:
        specs: list[dict] = []
        for p in ST.PRESETS:
            if p["label"] in labels:
                specs.append({"label": p["label"], **{k: v for k, v in p.items()
                                                      if k != "label"}})
        for lk in looks:
            for tp in tops:
                for rb in rebs:
                    specs.append({
                        "label": f"动量自定 L{lk}/T{tp}/R{rb}",
                        "type": "momentum",
                        "params": {"lookback": lk, "top": tp, "rebalance": rb},
                    })
        if not specs:
            st.warning("请至少选择一个预设或一组参数。")
            return

        rows = []
        prog = st.progress(0.0, text="回测中…")
        for i, spec in enumerate(specs):
            res = BT.run_backtest_from_spec(spec, closes, names)
            m = res.metrics
            if not m:
                continue
            row = {
                "策略": spec["label"],
                "年化收益": m["年化收益"],
                "最大回撤": m["最大回撤"],
                "夏普": m["夏普"],
                "卡玛": m["卡玛"],
                "月胜率": m["月胜率"],
                "年化换手": m.get("年化单边换手"),
            }
            if res.benchmark_metrics:
                row["超额年化"] = m["年化收益"] - res.benchmark_metrics["年化收益"]
            if use_split:
                cut = pd.Timestamp(split_date)
                for tag, seg in (("内", res.nav.loc[:cut]),
                                 ("外", res.nav.loc[cut:])):
                    sm = MT.compute_metrics(seg, rf=RISK_FREE)
                    row[f"年化(样本{tag})"] = sm.get("年化收益")
                    row[f"回撤(样本{tag})"] = sm.get("最大回撤")
            row["达标≥15%"] = "✅" if m["年化收益"] >= ANNUAL_TARGET else "❌"
            rows.append(row)
            prog.progress((i + 1) / len(specs), text=f"回测中… {i + 1}/{len(specs)}")
        st.session_state.cmp_df = pd.DataFrame(rows)

    if "cmp_df" not in st.session_state:
        return
    df = st.session_state.cmp_df
    if df.empty:
        st.warning("没有可展示的结果。")
        return
    df = df.sort_values("年化收益", ascending=False).reset_index(drop=True)
    st.subheader(f"结果排名（{len(df)} 组，按回测年化排序）")

    chart_cols = ["年化收益"]
    if "年化(样本外)" in df.columns and df["年化(样本外)"].notna().any():
        chart_cols = ["年化(样本内)", "年化(样本外)"]
    fig_cols = st.columns(len(chart_cols))
    for ax, col in zip(fig_cols, chart_cols):
        fig = go.Figure()
        fig.add_hline(y=ANNUAL_TARGET, line_dash="dot", line_color="#27ae60")
        fig.add_trace(go.Scatter(
            x=df["最大回撤"], y=df[col], mode="markers+text",
            text=[s if len(s) <= 16 else s[:15] + "…" for s in df["策略"]],
            textposition="top center", textfont=dict(size=9),
            marker=dict(size=11,
                        color=df["年化收益"],
                        colorscale="Viridis", showscale=False),
            customdata=np.stack([df["夏普"], df["年化换手"]], axis=1),
            hovertemplate="%{text}<br>最大回撤 %{x:.1%}<br>"
                          + col + " %{y:.1%}<br>夏普 %{customdata[0]:.2f}"
                          "<br>年化换手 %{customdata[1]:.0f}x<extra></extra>",
        ))
        fig.update_layout(height=430, xaxis_tickformat=".0%",
                          yaxis_tickformat=".0%", margin=dict(t=40),
                          xaxis_title="最大回撤", yaxis_title=col)
        ax.plotly_chart(fig, width="stretch")

    st.dataframe(
        df.style.format({
            "年化收益": "{:.1%}", "最大回撤": "{:.1%}", "月胜率": "{:.0%}",
            "夏普": "{:.2f}", "卡玛": "{:.2f}", "年化换手": "{:.1f}x",
            "超额年化": "{:+.1%}", "年化(样本内)": "{:.1%}",
            "年化(样本外)": "{:.1%}", "回撤(样本内)": "{:.1%}",
            "回撤(样本外)": "{:.1%}",
        }),
        width="stretch", height=420,
    )
    st.caption("样本内=切分日期之前，样本外=之后。样本外年化显著低于样本内 → 参数过拟合风险高。")
    st.caption("⚠️ 回测收益不代表未来实盘表现，不构成投资建议。")


# ---------- 页面 3：每日信号 ----------

def page_signals() -> None:
    st.header("🔔 每日信号")
    names = etf_pool()
    codes = list(names)

    if st.button("⟳ 更新行情数据", type="primary"):
        with st.status("更新数据中…", expanded=True) as status:
            def _on_status(code: str, msg: str) -> None:
                st.write(f"{code} {names.get(code, '')}: {msg}")
            D.update_cache(codes, BACKTEST_START, on_status=_on_status)
            status.update(label="数据更新完成", state="complete")
        st.session_state.data_version += 1
        _load_closes.clear()

    closes = get_closes()
    asof = closes.index[-1]
    # 数据过期提醒：最新数据距今天然日超过阈值时提示更新
    stale_days = (pd.Timestamp.today().normalize() - asof).days
    if stale_days > 7:
        st.warning(f"⚠️ 行情数据截至 {asof:%Y-%m-%d}，已 {stale_days} 天未更新，"
                   "请点击上方「更新行情数据」后再参考信号。")
    st.caption(f"数据截至 **{asof:%Y-%m-%d}**，共 {len(closes)} 个交易日。"
               "以下为各预设策略在最新交易日的信号。")

    sel = st.multiselect("启用策略", [p["label"] for p in ST.PRESETS],
                         default=[p["label"] for p in ST.PRESETS])

    # 当前持仓编辑
    saved = load_holdings()
    st.subheader("我的持仓（输入后生成买卖清单）")
    if "holdings_editor" not in st.session_state:
        h0 = saved.get("holdings", {})
        st.session_state.holdings_editor = pd.DataFrame(
            [{"代码": k, "市值(¥)": float(v)} for k, v in h0.items()]
            or [{"代码": "", "市值(¥)": 0.0}]
        )
    edited = st.data_editor(
        st.session_state.holdings_editor,
        num_rows="dynamic", key="he", width="stretch", height=180,
        column_config={
            "代码": st.column_config.TextColumn("代码"),
            "市值(¥)": st.column_config.NumberColumn("市值(¥)", format="%.0f"),
        },
    )
    cash = st.number_input("现金(¥)", min_value=0.0,
                           value=float(saved.get("cash", 0.0)), step=1000.0)
    if st.button("保存持仓"):
        hh = {}
        for _, r in edited.iterrows():
            code = str(r["代码"]).strip()
            if code and float(r["市值(¥)"] or 0) > 0:
                hh[code] = float(r["市值(¥)"])
        save_holdings(hh, cash)
        st.success("已保存（下次打开自动载入）")
    saved = load_holdings()

    st.divider()
    log_rows = []
    for p in ST.PRESETS:
        if p["label"] not in sel:
            continue
        spec = {"label": p["label"], **{k: v for k, v in p.items() if k != "label"}}
        sig = SG.latest_signal(spec, closes, names)
        res = BT.run_backtest_from_spec(spec, closes, names)
        with st.container(border=True):
            head = st.columns([3, 2])
            head[0].subheader(sig["label"])
            if "调仓" in sig["action"] and "无需" not in sig["action"]:
                head[1].warning(sig["action"], icon="🔔")
            else:
                head[1].success(sig["action"], icon="⏳")
            if sig["detail"]:
                st.caption(sig["detail"])
            # 该策略的历史回测指标（帮助评估信号可靠度）
            if res.metrics:
                mc = st.columns(4)
                mc[0].metric("回测年化", f"{res.metrics['年化收益']:.1%}",
                             f"{'达标' if res.metrics['年化收益'] >= ANNUAL_TARGET else '未达标'}")
                mc[1].metric("最大回撤", f"{res.metrics['最大回撤']:.1%}")
                mc[2].metric("夏普", f"{res.metrics['夏普']:.2f}")
                if res.benchmark_metrics:
                    mc[3].metric("超额年化",
                                 f"{res.metrics['年化收益'] - res.benchmark_metrics['年化收益']:+.1%}")
            tgt = sig["target"]
            if tgt:
                rows = [{"代码": c, "名称": names.get(c, c), "目标权重": w}
                        for c, w in sorted(tgt.items())]
                st.dataframe(pd.DataFrame(rows), hide_index=True,
                             width="stretch")
            else:
                st.markdown("**目标持仓：空仓持币**")
            hh = saved.get("holdings", {})
            if hh or saved.get("cash"):
                diff = SG.diff_holdings(hh, saved.get("cash", 0.0),
                                        tgt, names)
                if not diff.empty:
                    st.markdown("**操作清单**（与当前持仓的差额，±0.5% 以内不动）")
                    st.dataframe(
                        diff.style.format({
                            "当前权重": "{:.1%}", "目标权重": "{:.1%}",
                            "建议金额(¥)": "{:,.0f}",
                        }),
                        hide_index=True, width="stretch",
                    )
            log_rows.append({
                "日期": f"{asof:%Y-%m-%d}", "策略": sig["label"],
                "动作": sig["action"],
                "目标持仓": "; ".join(f"{c}:{w:.0%}" for c, w in
                                      sorted(tgt.items())) or "空仓",
            })

    # 信号历史日志：留档复盘用
    log_path = BASE_DIR / "signals_log.csv"
    lc1, _ = st.columns([1, 3])
    if log_rows and lc1.button("📝 记录今日信号到日志"):
        new_log = pd.DataFrame(log_rows)
        if log_path.exists():
            old = pd.read_csv(log_path, encoding="utf-8-sig")
            old = old[~((old["日期"] == f"{asof:%Y-%m-%d}")
                        & (old["策略"].isin([r["策略"] for r in log_rows])))]
            new_log = pd.concat([old, new_log], ignore_index=True)
        new_log.to_csv(log_path, index=False, encoding="utf-8-sig")
        st.success(f"已写入 {log_path.name}（同日重复记录自动去重）")
    if log_path.exists():
        with st.expander("📜 历史信号日志（signals_log.csv）"):
            st.dataframe(pd.read_csv(log_path, encoding="utf-8-sig"),
                         width="stretch", height=240)

    st.divider()
    spot = D.etf_spot()
    if spot is not None and "代码" in spot.columns:
        sub = spot[spot["代码"].isin(codes)][
            ["代码", "名称", "最新价", "涨跌幅"]].copy()
        sub["涨跌幅"] = sub["涨跌幅"] / 100.0
        st.subheader("最新行情参考（实时快照）")
        st.dataframe(
            sub.style.format({"涨跌幅": "{:+.2%}"}),
            hide_index=True, width="stretch", height=300,
        )


# ---------- 页面 4：数据管理 ----------

def page_data() -> None:
    st.header("🗂️ 数据管理")
    names = etf_pool()
    codes = list(names)

    st.subheader("当前 ETF 池")
    df = pd.DataFrame({"代码": codes, "名称": [names[c] for c in codes]})
    edited = st.data_editor(df, num_rows="dynamic", width="stretch",
                            key="pool_editor",
                            column_config={"代码": st.column_config.TextColumn("代码"),
                                           "名称": st.column_config.TextColumn("名称")})
    if st.button("保存 ETF 池"):
        new_pool = {}
        for _, r in edited.iterrows():
            code = str(r["代码"]).strip()
            nm = str(r["名称"]).strip() or code
            if code:
                new_pool[code] = nm
        save_etf_pool(new_pool)
        st.success(f"已保存 {len(new_pool)} 只 ETF；新增代码请点击下方「更新全部行情」下载数据。")
        st.rerun()

    st.divider()
    st.subheader("本地数据覆盖情况")
    cov = D.cache_coverage(codes)
    st.dataframe(cov, hide_index=True, width="stretch", height=300)

    c1, c2 = st.columns(2)
    if c1.button("⟳ 更新全部行情（增量）", type="primary"):
        with st.status("更新数据中…", expanded=True) as status:
            def _on(code: str, msg: str) -> None:
                st.write(f"{code} {names.get(code, '')}: {msg}")
            D.update_cache(codes, BACKTEST_START, on_status=_on)
            status.update(label="更新完成", state="complete")
        st.session_state.data_version += 1
        _load_closes.clear()
        st.rerun()
    if st.session_state.get("confirm_rebuild"):
        c2.error("重建将删除本地全部缓存并重新下载（1-2 分钟），再点一次确认。")
        if c2.button("⚠️ 确认删除并重建缓存"):
            import shutil
            shutil.rmtree(D.DATA_DIR, ignore_errors=True)
            st.session_state.data_version += 1
            _load_closes.clear()
            with st.spinner("重新下载…"):
                D.update_cache(codes, BACKTEST_START)
            st.session_state.confirm_rebuild = False
            st.success("重建完成")
            st.rerun()
    elif c2.button("🗑 重建缓存（全量重新下载）"):
        st.session_state.confirm_rebuild = True
        st.rerun()

    st.divider()
    st.caption(
        "数据来源：akshare（东方财富公开接口，免费）。日线为后复权价格，"
        "直接用其比例计算收益率即包含分红再投资。接口偶发限流，已内置重试；"
        "增量更新失败不影响已有缓存。"
    )


# ---------- 入口 ----------

with st.sidebar:
    st.title("📈 A股 ETF 量化工具")
    page = st.radio("导航", ["📊 回测", "⚖️ 策略对比", "🔔 每日信号", "🗂️ 数据管理"],
                    label_visibility="collapsed")
    st.divider()
    st.caption(
        "⚠️ 免责声明：本工具仅供研究学习。历史回测收益不代表未来实盘表现，"
        f"年化 ≥ {ANNUAL_TARGET:.0%} 仅为回测筛选门槛，不构成投资建议或收益承诺。"
    )

if page == "📊 回测":
    page_backtest()
elif page == "⚖️ 策略对比":
    page_compare()
elif page == "🔔 每日信号":
    page_signals()
else:
    page_data()
