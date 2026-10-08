"""Streamlit 页面功能测试（无浏览器环境下的界面验证）。

运行：python test_app.py
用 streamlit.testing.v1.AppTest 逐页执行 app.py，验证无异常、关键元素在位，
并模拟「运行回测」与页面切换交互。
"""
from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

PAGES = ["📊 回测", "⚖️ 策略对比", "🔔 每日信号", "🗂️ 数据管理"]


def check_page(page: str) -> None:
    at = AppTest.from_file("app.py", default_timeout=300)
    at.run()
    assert not at.exception, f"{page} 初始渲染异常: {at.exception}"
    assert at.sidebar.radio[0].value == PAGES[0]
    at.sidebar.radio[0].set_value(page)
    at.run()
    assert not at.exception, f"{page} 切换后异常: {at.exception}"
    assert at.sidebar.radio[0].value == page
    print(f"[ok] {page} 渲染无异常")


def check_backtest_run() -> None:
    at = AppTest.from_file("app.py", default_timeout=300)
    at.run()
    at.sidebar.radio[0].set_value(PAGES[0])
    at.run()
    # 表单内改参数（保持默认即可），提交运行回测
    submit = [b for b in at.button if b.type == "primary" or "运行回测" in (b.label or "")]
    assert submit, "找不到运行回测按钮"
    submit[0].click()
    at.run()
    assert not at.exception, f"回测运行异常: {at.exception}"
    # 成功标志：出现指标卡片（年化收益 metric）或达标/未达标提示
    texts = [el.value for el in at.markdown] + [el.body for el in at.success] + \
            [el.body for el in at.error] + [c.value for c in at.metric]
    assert any("年化" in t for t in texts), "回测结果未渲染"
    print("[ok] 回测页交互：运行回测并渲染结果")


def check_compare_run() -> None:
    at = AppTest.from_file("app.py", default_timeout=600)
    at.run()
    at.sidebar.radio[0].set_value(PAGES[1])
    at.run()
    submit = [b for b in at.button if "批量回测" in (b.label or "")]
    assert submit, "找不到批量回测按钮"
    submit[0].click()
    at.run()
    assert not at.exception, f"批量回测异常: {at.exception}"
    assert at.dataframe, "对比结果表未渲染"
    rows = at.dataframe[0].value
    assert rows is not None and len(rows) >= 4, f"对比结果行数异常: {None if rows is None else len(rows)}"
    print(f"[ok] 策略对比页交互：批量回测 {len(rows)} 行结果")


if __name__ == "__main__":
    for p in PAGES:
        check_page(p)
    check_backtest_run()
    check_compare_run()
    print("APP TESTS PASSED")
    sys.exit(0)
