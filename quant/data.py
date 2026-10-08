"""行情数据层。

数据源（均为免费公开接口）：
1. 主源：腾讯行情 fqkline（web.ifzq.gtimg.cn），单次上限 800 行，自动翻页；
   复权序列为「后锚定」口径——序列内收益率正确（含分红），但每次有新
   分红事件后全历史会被重新缩放，因此缓存策略是【全量刷新、整体替换】，
   不做增量拼接。
2. 备源：akshare 的东方财富 ETF 日线（真后复权）。主源失败时自动切换。

缓存：每个 ETF 一份 parquet（open/close/high/low/volume）。
所有请求带重试与节流；国内站点一律直连（绕过本机代理客户端）。
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

# 数据源均为国内站点，强制直连：本机代理客户端（如 Clash）对国内接口
# 不稳定且无必要；部分安全软件也会干扰代理链路
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import akshare as ak
import pandas as pd
import requests

from config import BENCHMARK, DATA_DIR

_TX_BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_TX_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://gu.qq.com/",
}
_TX_MAX_ROWS = 800   # 腾讯接口单次最大行数（实测 800 可用，>1000 回落 640）


def _retry(fn: Callable[[], pd.DataFrame], attempts: int = 5, wait: float = 2.0) -> pd.DataFrame:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # 网络接口的异常种类繁多，统一重试
            last = e
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"接口重试 {attempts} 次仍失败: {last}") from last


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm_date(s: str) -> str:
    return pd.Timestamp(s).strftime("%Y-%m-%d")


def _tx_symbol(code: str) -> str:
    """510300→sh510300，159915→sz159915（按首位数字判断交易所）。"""
    prefix = "sh" if code and code[0] in "569" else "sz"
    return prefix + code


def _tx_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False  # 不吃系统代理
    sess.headers.update(_TX_HEADERS)
    return sess


def _tx_page(sess: requests.Session, sym: str, start: str, end: str) -> list:
    """取一个 800 行以内的批次（返回区间内最后 count 行）。"""
    r = sess.get(
        _TX_BASE,
        params={"param": f"{sym},day,{start},{end},{_TX_MAX_ROWS},hfq"},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    inner = (j.get("data") or {}).get(sym) or {}
    if not isinstance(inner, dict):
        raise RuntimeError(f"腾讯接口返回异常: {str(j)[:120]}")
    key = next((k for k in ("hfqday", "qfqday", "day") if k in inner), None)
    return inner.get(key, []) if key else []


def _fetch_tencent(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """腾讯 fqkline 全历史抓取：从 end 向前按 800 行翻页到 start。"""
    sym = _tx_symbol(code)
    start_date, end_date = _norm_date(start_date), _norm_date(end_date)
    sess = _tx_session()
    rows: list = []
    end = end_date
    for _ in range(60):  # 800×60=4.8 万行上限，防死循环
        batch = _retry(lambda: _tx_page(sess, sym, start_date, end))
        if not batch:
            break
        rows = batch + rows
        first = str(batch[0][0])
        if len(batch) < _TX_MAX_ROWS or first <= start_date:
            break
        end = (pd.Timestamp(first) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        time.sleep(0.3)  # 节流
    if not rows:
        raise RuntimeError(f"腾讯接口未返回 {code} 的数据")

    recs = [(str(r[0]), float(r[1]), float(r[2]), float(r[3]),
             float(r[4]), float(r[5])) for r in rows]
    df = pd.DataFrame(recs, columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.loc[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
    return df[["open", "close", "high", "low", "volume"]].astype(float)


def _fetch_eastmoney(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """备用源：akshare 东方财富 ETF 后复权日线。"""
    df = _retry(lambda: ak.fund_etf_hist_em(
        symbol=code, period="daily",
        start_date=_norm_date(start_date).replace("-", ""),
        end_date=_norm_date(end_date).replace("-", ""),
        adjust="hfq",
    ))
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df[["open", "close", "high", "low", "volume"]].astype(float)


def fetch_etf_daily(code: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """下载单只 ETF 的复权日线（主源腾讯，失败切东财备用）。"""
    end = _norm_date(end_date) if end_date else _today()
    try:
        return _fetch_tencent(code, start_date, end)
    except Exception:
        return _fetch_eastmoney(code, start_date, end)


def cache_file(code: str) -> Path:
    return DATA_DIR / f"{code}.parquet"


def load_cached(code: str) -> pd.DataFrame | None:
    f = cache_file(code)
    if not f.exists():
        return None
    return pd.read_parquet(f)


def update_cache(
    codes: Iterable[str], start_date: str, on_status: Callable[[str, str], None] | None = None
) -> dict[str, str]:
    """全量刷新缓存（每次整体替换，保证复权口径一致）。

    返回每个代码的状态说明。单只失败不影响其他标的与已有缓存。
    """
    DATA_DIR.mkdir(exist_ok=True)
    statuses: dict[str, str] = {}
    for code in codes:
        try:
            df = fetch_etf_daily(code, start_date)
            df.to_parquet(cache_file(code))
            statuses[code] = f"刷新 {len(df)} 条（{df.index[0]:%Y-%m-%d} 起）"
        except Exception as e:
            statuses[code] = f"失败: {e}"
        if on_status:
            on_status(code, statuses[code])
        time.sleep(0.2)
    return statuses


def build_close_matrix(
    codes: Iterable[str],
    start: str | None = None,
    end: str | None = None,
    calendar_code: str = BENCHMARK,
    column: str = "close",
) -> pd.DataFrame:
    """对齐的价格矩阵（复权），列=代码，行=交易日。

    column: "close"（默认）或 "open" 等，取缓存中的对应价格列。
    以基准 ETF（默认沪深300）的交易日为日历；ETF 未上市的日期为 NaN。
    本地无任何缓存时抛出异常提示先更新数据。
    """
    frames: dict[str, pd.Series] = {}
    for code in codes:
        df = load_cached(code)
        if df is not None and not df.empty and column in df.columns:
            frames[code] = df[column]
    if not frames:
        raise RuntimeError("本地没有任何行情缓存，请先在「数据管理」页或 run_daily.py 更新数据")

    cal = frames.get(calendar_code)
    if cal is None:
        # 基准不在池内时，取历史最长的一列作为日历
        cal = max(frames.values(), key=len)
    if start is not None:
        cal = cal.loc[cal.index >= pd.Timestamp(start)]
    if end is not None:
        cal = cal.loc[cal.index <= pd.Timestamp(end)]
    if cal.empty:
        raise RuntimeError("给定起止日期内没有交易日，请检查日期范围")

    closes = pd.DataFrame({code: s.reindex(cal.index) for code, s in frames.items()})
    closes.index.name = "date"
    return closes


def etf_spot() -> pd.DataFrame | None:
    """全部 ETF 的实时快照（东财），用于信号页展示最新价；失败时返回 None。"""
    try:
        return _retry(lambda: ak.fund_etf_spot_em())
    except Exception:
        return None


def cache_coverage(codes: Iterable[str]) -> pd.DataFrame:
    """各 ETF 本地缓存的覆盖情况（起止日期、条数、最新收盘价）。"""
    rows = []
    for code in codes:
        df = load_cached(code)
        if df is None or df.empty:
            rows.append({"代码": code, "状态": "无缓存"})
        else:
            rows.append({
                "代码": code,
                "状态": "有缓存",
                "起始日期": df.index[0].strftime("%Y-%m-%d"),
                "最新日期": df.index[-1].strftime("%Y-%m-%d"),
                "交易日数": len(df),
                "最新收盘(复权)": round(float(df["close"].iloc[-1]), 3),
            })
    return pd.DataFrame(rows)
