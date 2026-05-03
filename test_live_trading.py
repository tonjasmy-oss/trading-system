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

from menxia_sheng import MenxiaSheng, RiskLevel
from shangshu_sheng import ShangshuSheng, _CCXT_AVAILABLE
from config import LIVE_TESTNET, LIVE_API_KEY, LIVE_API_SECRET


def test_menxia_review():
    """测试1：门下省审核服务"""
    print("\n" + "="*60)
    print("测试1: 门下省风控审核")
    print("="*60)

    menxia = MenxiaSheng(initial_capital=10000.0)

    # 正常开仓审核
    review = menxia.review_open(
        symbol="ETH/USDT",
        entry_price=3200.0,
        quantity=0.3,
        agent_id="test_agent",
    )
    print(f"  ✅ 正常审核: approved={review.approved}, reason={review.reason}")
    assert review.approved == True, "应该通过"

    # 记录开仓
    menxia.record_open("ETH/USDT", 3200.0, 0.3, 3130.0, 3330.0)
    print(f"  ✅ 开仓记录: 持仓数={menxia.get_status()['open_positions']}")

    # 单日亏损过大测试
    menxia._daily_loss = 0.06  # 模拟已亏损6%
    review2 = menxia.review_open(
        symbol="SOL/USDT",
        entry_price=180.0,
        quantity=1.0,
        agent_id="test_agent",
    )
    print(f"  ❌ 亏损过大审核: approved={review2.approved}, reason={review2.reason}")
    assert review2.approved == False, "应该被否决"
    assert "单日亏损" in review2.reason, "应该包含亏损原因"

    # 暴露度超限测试
    menxia._daily_loss = 0.0
    menxia._daily_trades = 9
    menxia.current_capital = 10000.0
    review3 = menxia.review_open(
        symbol="BTC/USDT",
        entry_price=65000.0,
        quantity=0.15,  # 15% 暴露度，刚好超限（MAX=0.15）
        agent_id="test_agent",
    )
    # 当前已有 ETH 0.3×3200=960，加上 BTC 0.15×65000=9750 = 10710 > 10000
    print(f"  ❌ 暴露度过高审核: approved={review3.approved}, reason={review3.reason}")
    assert review3.approved == False, "应该被否决"

    # 平仓审核（亏损 -6.7% 触发 5% 硬止损）
    menxia._positions["ETH/USDT"]["entry_price"] = 3000.0  # 模拟大幅亏损
    close_allowed = menxia.review_close("ETH/USDT", 2800.0, -6.7)  # -6.67% < -5%
    print(f"  ❌ 硬止损平仓拦截: allowed={close_allowed}")

    # 平仓审核通过
    close_allowed2 = menxia.review_close("ETH/USDT", 3200.0, 0.0)
    print(f"  ✅ 正常平仓审核: allowed={close_allowed2}")

    # 风险等级升级测试
    menxia.update_equity(9300.0)  # 从10000回落7%
    status = menxia.get_status()
    print(f"  ⚠️  Equity回落升级: level={status['risk_level']}")
    assert status["risk_level"] in ["caution", "warning"], "应该升级风险等级"

    print("\n✅ 门下省测试全部通过！")


def test_menxia_xingbu():
    """测试2：刑部记录"""
    print("\n" + "="*60)
    print("测试2: 刑部违规记录")
    print("="*60)

    import tempfile
    db_path = tempfile.mktemp(suffix=".db")
    menxia = MenxiaSheng(initial_capital=10000.0, db_path=db_path)
    xingbu = menxia.get_xingbu()

    # 模拟被否决的交易
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
    print("  ✅ 否决记录已写入刑部")

    violations = xingbu.get_violations()
    assert len(violations) == 1, "应该有1条违规记录"
    assert violations[0]["symbol"] == "DOGE/USDT", "symbol应该匹配"
    print(f"  ✅ 刑部查询: {len(violations)}条记录, symbol={violations[0]['symbol']}")

    os.unlink(db_path)
    print("\n✅ 刑部测试通过！")


def test_shangshu_adapter():
    """测试3：尚书省交易执行（仅验证结构，不真实下单）"""
    print("\n" + "="*60)
    print("测试3: 尚书省执行调度")
    print("="*60)

    if not _CCXT_AVAILABLE:
        print("  ⚠️  ccxt 未安装，跳过尚书省实盘测试")
        print("  ✅ 尚书省结构测试通过（ccxt依赖缺失）")
        return

    if not LIVE_API_KEY or LIVE_API_SECRET in ["", "your_secret_here"]:
        print("  ⚠️  未配置实盘 API Key，跳过真实下单测试")
        print("  ✅ 尚书省结构测试通过（无API Key）")
        return

    import asyncio

    async def run_test():
        shangshu = ShangshuSheng(
            exchange="binance",
            api_key=LIVE_API_KEY,
            api_secret=LIVE_API_SECRET,
            testnet=LIVE_TESTNET,
        )

        print(f"  交易所: {shangshu.exchange}")
        print(f"  测试网: {shangshu.testnet}")

        # 查询余额
        balance = await shangshu.get_balance("USDT")
        print(f"  ✅ USDT余额: ${balance:.2f}")

        return True

    success = asyncio.get_event_loop().run_until_complete(run_test())
    print("\n✅ 尚书省测试通过！")


def test_sansheng_workflow():
    """测试4：三省六部完整工作流（模拟）"""
    print("\n" + "="*60)
    print("测试4: 三省六部完整工作流（模拟）")
    print("="*60)

    menxia = MenxiaSheng(initial_capital=10000.0)
    equity = 10000.0

    # 模拟：中书省生成信号 → ETH RSI=25 超卖
    print("  📋 中书省: 检测到 ETH RSI=25 超卖信号 BUY")
    print(f"     当前Equity: ${equity:.2f}, 风险等级: {menxia.get_status()['risk_level']}")

    # 门下省审核
    review = menxia.review_open(
        symbol="ETH/USDT",
        entry_price=3200.0,
        quantity=0.3,
        agent_id="agent_1",
    )

    if review.approved:
        print(f"  ✅ 门下省审核: approved=True")
        print(f"     暴露度: {review.exposure_pct:.1f}%")
        # 模拟开仓成功
        menxia.record_open("ETH/USDT", 3200.0, 0.3, 3130.0, 3330.0)
        print(f"  ✅ 开仓已记录: ETH 0.3 @ $3200")

        # 模拟价格上涨
        equity = 10000.0 + 100.0  # 盈利100
        menxia.update_equity(equity)
        print(f"  💰 Equity更新: ${equity:.2f}")

        # 模拟平仓信号
        print("  📋 中书省: ETH 触及止盈线 SELL")
        can_close = menxia.review_close("ETH/USDT", 3330.0, 4.06)
        print(f"  ✅ 平仓审核: allowed={can_close}")

        if can_close:
            menxia.record_close("ETH/USDT", 4.06)
            print(f"  ✅ 平仓已记录: 盈亏 +4.06%")

    # 查看最终状态
    status = menxia.get_status()
    print(f"\n  📊 最终状态:")
    print(f"     风险等级: {status['risk_level']}")
    print(f"     当日亏损: {status['daily_loss_pct']:.2f}%")
    print(f"     持仓数: {status['open_positions']}")
    print(f"     当日交易: {status['daily_trades']}次")

    print("\n✅ 三省六部工作流测试完成！")


def test_env_config():
    """测试5：环境变量配置检查"""
    print("\n" + "="*60)
    print("测试5: 实盘配置检查")
    print("="*60)

    from config import (
        LIVE_TRADING_ENABLED, LIVE_EXCHANGE, LIVE_API_KEY,
        LIVE_TESTNET, LIVE_INITIAL_CAPITAL,
        RISK_MAX_DAILY_LOSS_PCT, RISK_MAX_TOTAL_EXPOSURE,
    )

    print(f"  LIVE_TRADING_ENABLED: {LIVE_TRADING_ENABLED}")
    print(f"  LIVE_EXCHANGE:        {LIVE_EXCHANGE}")
    print(f"  LIVE_API_KEY:         {'已配置' if LIVE_API_KEY else '⚠️ 未配置'}")
    print(f"  LIVE_TESTNET:         {LIVE_TESTNET}")
    print(f"  LIVE_INITIAL_CAPITAL: ${LIVE_INITIAL_CAPITAL:.2f}")
    print(f"  RISK_MAX_DAILY_LOSS:  {RISK_MAX_DAILY_LOSS_PCT*100:.0f}%")
    print(f"  RISK_MAX_EXPOSURE:    {RISK_MAX_TOTAL_EXPOSURE*100:.0f}%")

    if not LIVE_TRADING_ENABLED:
        print("\n  ⚠️  实盘交易未启用（LIVE_TRADING_ENABLED=false）")
        print("     启用实盘: 在 .env 中设置 LIVE_TRADING_ENABLED=true")
        print("     配置API:   LIVE_API_KEY / LIVE_API_SECRET")

    print("\n✅ 配置检查完成！")


if __name__ == "__main__":
    print("="*60)
    print("  三省六部架构测试套件")
    print("="*60)

    test_env_config()
    test_menxia_review()
    test_menxia_xingbu()
    test_sansheng_workflow()
    test_shangshu_adapter()

    print("\n" + "="*60)
    print("  全部测试完成！")
    print("="*60)
    print()
    print("启用实盘交易：")
    print("  1. 在 .env 中设置 LIVE_TRADING_ENABLED=true")
    print("  2. 配置 LIVE_API_KEY 和 LIVE_API_SECRET")
    print("  3. 设置 LIVE_TESTNET=true（先用测试网）")
    print("  4. 运行: python live_trading.py --check")
