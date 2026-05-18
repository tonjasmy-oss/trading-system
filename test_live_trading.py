"""
实盘交易流程测试
================
测试三省六部架构的完整流程：
  中书省信号 → 门下省审核 → 尚书省执行 → 刑部记录

使用方法：
  python test_live_trading.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tempfile
import pytest

from menxia_sheng import MenxiaSheng, RiskLevel
from shangshu_sheng import ShangshuSheng, _CCXT_AVAILABLE
from config import LIVE_TESTNET, LIVE_API_KEY, LIVE_API_SECRET


@pytest.fixture
def menxia_db(tmp_path):
    """每个测试独立临时数据库，避免进程间 race condition"""
    db = str(tmp_path / "test.db")
    menxia = MenxiaSheng(initial_capital=10000.0, db_path=db)
    yield menxia, db
    # 关闭连接后清理
    try:
        menxia._get_conn().close()
    except Exception:
        pass


def test_menxia_review(menxia_db):
    """测试1：门下省审核服务"""
    menxia, _ = menxia_db

    # 正常开仓审核
    review = menxia.review_open(
        symbol="ETH/USDT",
        entry_price=3200.0,
        quantity=0.3,
        agent_id="test_agent",
    )
    assert review.approved == True, "应该通过"

    # 记录开仓（多头）
    menxia.record_open("ETH/USDT", 3200.0, 0.3, 3130.0, 3330.0, side="long")

    # 单日亏损过大测试
    menxia._daily_loss = 0.06  # 模拟已亏损6%
    review2 = menxia.review_open(
        symbol="SOL/USDT",
        entry_price=180.0,
        quantity=1.0,
        agent_id="test_agent",
    )
    assert review2.approved == False, "应该被否决"
    assert "单日亏损" in review2.reason, "应该包含亏损原因"

    # 暴露度超限测试（ETH多头 0.3×3200=960，BTC多头 0.15×65000=9750，总计10710 > 10000）
    menxia._daily_loss = 0.0
    menxia._daily_trades = 9
    menxia.current_capital = 10000.0
    review3 = menxia.review_open(
        symbol="BTC/USDT",
        entry_price=65000.0,
        quantity=0.15,
        agent_id="test_agent",
    )
    assert review3.approved == False, "应该被否决"

    # 平仓审核（亏损 -6.7% 触发 5% 硬止损）
    menxia._positions["ETH/USDT"]["entry_price"] = 3000.0
    close_allowed = menxia.review_close("ETH/USDT", 2800.0, -6.7)
    assert close_allowed == False, "应被硬止损拦截"

    # 平仓审核通过
    close_allowed2 = menxia.review_close("ETH/USDT", 3200.0, 0.0)
    assert close_allowed2 == True, "正常平仓应通过"

    # 风险等级升级测试
    menxia.update_equity(9300.0)
    status = menxia.get_status()
    assert status["risk_level"] in ["caution", "warning"], "应该升级风险等级"


def test_menxia_xingbu(tmp_path):
    """测试2：刑部记录"""
    db_path = str(tmp_path / "xingbu.db")
    menxia = MenxiaSheng(initial_capital=10000.0, db_path=db_path)
    xingbu = menxia.get_xingbu()

    from menxia_sheng import ExecutionOrder
    order = ExecutionOrder(
        order_id="test_reject_001",
        agent_id="test_agent",
        symbol="DOGE/USDT",
        side="BUY",
        quantity=1000.0,
        order_type="market",
        entry_price=0.15,
    )
    xingbu.record_rejection(order, "单日亏损超限", RiskLevel.CAUTION, ["R1_CAUTION:5%"])

    violations = xingbu.get_violations()
    assert len(violations) == 1, "应该有1条违规记录"
    assert violations[0]["symbol"] == "DOGE/USDT", "symbol应该匹配"


def test_menxia_short_exposure(menxia_db):
    """测试：空头暴露度计算（多空混合持仓，验证绝对值相加）"""
    menxia, _ = menxia_db
    # 资金 = $10,000
    # MAX_POSITION_PER_SYMBOL = 15%，MAX_TOTAL_EXPOSURE = 30%

    # ETH 多头 0.2 × $4800 = $960，占比 9.6%（< 15%，R4通过）
    menxia.record_open("ETH/USDT", 4800.0, 0.2, 4700.0, 4900.0, side="long")

    # BTC 空头 0.02 × $65000 = $1300，占比 13%（< 15%，R4通过）
    # 总暴露度 = ETH多头960 + BTC空头1300 = 2260，占比 22.6%（< 30%，R3通过）
    review = menxia.review_open(
        symbol="BTC/USDT",
        entry_price=65000.0,
        quantity=0.02,
        agent_id="test_agent",
        side="short",
    )
    assert review.approved == True, f"空头暴露度应正确计入绝对值：{review.reason}"
    assert review.exposure_pct > 20.0, f"暴露度应为绝对值相加：{review.exposure_pct:.1f}%"
    assert "R3" not in str(review.rules_triggered), "总暴露度不应触发"

    # 再加 SOL 空头，使总暴露度超限
    # 已有：ETH多头960 + BTC空头1300 = 2260
    # SOL空头 0.2 × $500 = $100，占比 1%
    # 总：2260 + 100 = 2360，占比 23.6% — 仍未超 30%
    # 加 SOL空头 1.0 × $500 = $500：总 2760，占比 27.6% — 仍未超
    # 换大标的：SOL空头 5.0 × $500 = $2500：总 4760，占比 47.6% >> 30%
    review_over = menxia.review_open(
        symbol="SOL/USDT",
        entry_price=500.0,
        quantity=5.0,
        agent_id="test_agent",
        side="short",
    )
    assert review_over.approved == False, "总暴露度超限应被否决"
    assert any("R3" in r for r in review_over.rules_triggered), "应有R3总暴露度规则触发"


def test_sansheng_workflow(menxia_db):
    """测试：三省六部完整工作流（模拟）"""
    menxia, _ = menxia_db
    equity = 10000.0

    review = menxia.review_open(
        symbol="ETH/USDT",
        entry_price=3200.0,
        quantity=0.3,
        agent_id="agent_1",
    )
    assert review.approved == True

    menxia.record_open("ETH/USDT", 3200.0, 0.3, 3130.0, 3330.0, side="long")

    equity = 10000.0 + 100.0
    menxia.update_equity(equity)

    can_close = menxia.review_close("ETH/USDT", 3330.0, 4.06)
    assert can_close == True

    menxia.record_close("ETH/USDT", 4.06)
    status = menxia.get_status()
    assert status["open_positions"] == 0


def test_env_config():
    """测试：环境变量配置检查"""
    from config import (
        LIVE_TRADING_ENABLED, LIVE_EXCHANGE, LIVE_API_KEY,
        LIVE_TESTNET, LIVE_INITIAL_CAPITAL,
        RISK_MAX_DAILY_LOSS_PCT, RISK_MAX_TOTAL_EXPOSURE,
    )
    # 不抛异常即通过
    assert LIVE_INITIAL_CAPITAL > 0
    assert RISK_MAX_DAILY_LOSS_PCT > 0


def test_shangshu_adapter_structure():
    """测试：尚书省各 Adapter 结构完整性（mock，无真实网络）"""
    import asyncio
    from shangshu_sheng import (
        BinanceAdapter, GateioAdapter, BybitAdapter,
        HyperliquidAdapter, ExecutionResult,
    )

    class FakeExchange:
        """模拟 ccxt exchange，验证接口契约"""
        def load_markets(self):
            pass

        def create_order(self, symbol, order_type, side, amount, price=None):
            # 模拟市价单：filled=[]，average=0（Bug#4 场景）
            return {
                "id": "mock_order_001", "symbol": symbol, "side": side,
                "amount": amount, "filled": [], "average": 0, "status": "closed",
            }

        def fetch_order(self, order_id, symbol):
            return {"id": order_id, "symbol": symbol, "status": "closed",
                    "filled": [], "average": 0}

        def fetch_balance(self):
            return {"USDT": {"total": 10000.0, "free": 8000.0, "used": 2000.0}}

    async def run():
        # 验证 BinanceAdapter.place_order — fills=[] 时 avg_price fallback 不过 0
        adapter = BinanceAdapter(api_key="test", api_secret="test", testnet=True)
        adapter._exchange = FakeExchange()

        result = await adapter.place_order("ETH/USDT", "BUY", "MARKET", 0.1, price=3200.0)
        assert result.success
        assert result.exec_price > 0, "avg_price fallback 应使用 price 参数"

        # 验证各 Adapter.fetch_balance 返回 ccxt 风格余额
        for cls, kwargs in [
            (GateioAdapter,      {"api_key": "k", "api_secret": "s", "testnet": True}),
            (BybitAdapter,       {"api_key": "k", "api_secret": "s", "testnet": True}),
            (HyperliquidAdapter, {"api_key": "k", "api_secret": "s", "testnet": True}),
        ]:
            a = cls(**kwargs)
            a._exchange = FakeExchange()
            b = await a.fetch_balance()
            assert "USDT" in b, f"{cls.__name__}.fetch_balance 应返回 ccxt 风格余额"
            assert "total" in b["USDT"], f"{cls.__name__}.fetch_balance['USDT'] 应有 total 字段"

        return True

    ok = asyncio.get_event_loop().run_until_complete(run())
    assert ok
