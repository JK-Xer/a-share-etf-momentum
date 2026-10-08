"""全局配置。

默认值写死在本文件；界面「数据管理」页对 ETF 池的修改会保存到
user_config.json，加载时覆盖默认池。当前持仓（每日信号页）同样保存在
user_config.json。策略参数与成本在界面中即时调整、不持久化。
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
USER_CONFIG_PATH = BASE_DIR / "user_config.json"

# 默认 ETF 池：宽基 + 行业 + 避险 + 跨境，均为高流动性场内品种
DEFAULT_ETF_POOL: dict[str, str] = {
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "159915": "创业板ETF",
    "588000": "科创50ETF",
    "512100": "中证1000ETF",
    "510880": "红利ETF",
    "518880": "黄金ETF",
    "511010": "国债ETF",
    "512880": "证券ETF",
    "512690": "酒ETF",
    "512010": "医药ETF",
    "512480": "半导体ETF",
    # 跨境 QDII（T+0 交易；注意溢价风险，见操作说明书）
    "513100": "纳指ETF",
    "513500": "标普500ETF",
    "513050": "中概互联ETF",
    "513130": "恒生科技ETF",
}

BENCHMARK = "510300"      # 对比基准：沪深300ETF 买入持有
BACKTEST_START = "2015-01-01"
ANNUAL_TARGET = 0.15      # 策略合格门槛：回测年化 ≥ 15%
RISK_FREE = 0.02          # 夏普比率的无风险利率
# 单边交易成本（ETF 免印花税）。万3 = 佣金约万1 + 点差/冲击约万2，
# 对高流动性宽基 ETF 是贴近实盘的假设；佣金较高或流动性一般的
# 标的请自行调高——成本每增加万2/边，动量类策略年化约降 1 个点。
COMMISSION = 0.0001
SLIPPAGE = 0.0002
TRADING_DAYS = 252        # 年化用的每年交易日数


def load_user_config() -> dict:
    if USER_CONFIG_PATH.exists():
        try:
            return json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_user_config(cfg: dict) -> None:
    USER_CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def etf_pool() -> dict[str, str]:
    """当前生效的 ETF 池（用户保存过的覆盖默认池）。"""
    saved = load_user_config().get("etf_pool")
    if isinstance(saved, dict) and saved:
        return {str(k): str(v) for k, v in saved.items()}
    return dict(DEFAULT_ETF_POOL)


def save_etf_pool(pool: dict[str, str]) -> None:
    cfg = load_user_config()
    cfg["etf_pool"] = {str(k): str(v) for k, v in pool.items()}
    save_user_config(cfg)


def load_holdings() -> dict:
    """每日信号页保存的当前持仓：{holdings: {代码: 市值}, cash: 现金}"""
    return load_user_config().get("holdings", {})


def save_holdings(holdings: dict, cash: float) -> None:
    cfg = load_user_config()
    cfg["holdings"] = {"holdings": holdings, "cash": float(cash)}
    save_user_config(cfg)
