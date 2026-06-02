"""
Skill Handlers - 各 skill 的具体处理逻辑

每个 handler 接收 (action, params, user_message)，
返回 dict，包含 response 等字段。
"""

import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


def handle_trader_management(action: Optional[str], params: dict, user_message: str) -> dict:
    """交易员管理 skill handler"""
    # TODO: 对接现有 trader 管理接口
    # from agent.shangshu_sheng import TraderManager
    
    if action == "list":
        return {
            "skill_id": "trader_management",
            "response": "当前交易员列表：\n1. SUI-ATRSTOP (运行中) - 收益率 +2.2%\n2. BTC-RSING (已停止) - 收益率 -0.3%",
            "continue_skill": False
        }
    
    if action == "create":
        trader_name = params.get("trader_name", "未命名")
        symbol = params.get("symbol", "SUI/USDT")
        strategy = params.get("strategy", "ATRSTOP")
        exchange = params.get("exchange", "binance")
        
        return {
            "skill_id": "trader_management",
            "response": f"好的，正在创建交易员：{trader_name}\n交易对: {symbol}\n策略: {strategy}\n交易所: {exchange}\n\n请确认是否立即启动？",
            "needs_confirmation": True,
            "continue_skill": True
        }
    
    if action == "start":
        trader_name = params.get("trader_name", "")
        if trader_name:
            return {
                "skill_id": "trader_management",
                "response": f"✅ 交易员 {trader_name} 已启动",
                "needs_confirmation": False,
                "continue_skill": False
            }
        return {
            "skill_id": "trader_management",
            "response": "请告诉我要启动哪个交易员？",
            "continue_skill": True
        }
    
    if action == "stop":
        trader_name = params.get("trader_name", "")
        return {
            "skill_id": "trader_management",
            "response": f"✅ 交易员 {trader_name} 已停止",
            "needs_confirmation": False,
            "continue_skill": False
        }
    
    if action == "delete":
        return {
            "skill_id": "trader_management",
            "response": "⚠️ 确定要删除这个交易员吗？此操作不可恢复。\n\n请回复\"确认删除\"以继续。",
            "needs_confirmation": True,
            "continue_skill": True
        }
    
    return {
        "skill_id": "trader_management",
        "response": "交易员管理支持：创建、启动、停止、编辑、删除。请告诉我你要做什么？",
        "continue_skill": True
    }


def handle_exchange_management(action: Optional[str], params: dict, user_message: str) -> dict:
    """交易所配置管理 skill handler"""
    if action == "list":
        return {
            "skill_id": "exchange_management",
            "response": "已配置的交易所：\n1. Binance (主账户) ✅\n2. Bybit (测试网) ✅\n3. OKX - 未配置",
            "continue_skill": False
        }
    
    if action == "test":
        exchange = params.get("exchange", "binance")
        return {
            "skill_id": "exchange_management",
            "response": f"正在测试 {exchange} 连接...\n✅ 连接成功！余额: 1000.00 USDT",
            "continue_skill": False
        }
    
    if action == "add":
        exchange = params.get("exchange", "")
        return {
            "skill_id": "exchange_management",
            "response": f"好的，要配置 {exchange} 交易所。\n\n请提供以下信息：\n1. API Key\n2. API Secret\n3. Passphrase (如需要)",
            "continue_skill": True
        }
    
    return {
        "skill_id": "exchange_management",
        "response": "交易所管理支持：添加配置、测试连接、删除配置。请告诉我你要做什么？",
        "continue_skill": True
    }


def handle_model_management(action: Optional[str], params: dict, user_message: str) -> dict:
    """AI 模型配置管理 skill handler"""
    if action == "list":
        return {
            "skill_id": "model_management",
            "response": "已配置的模型：\n1. DeepSeek-V3 (默认) ✅\n2. Qwen-72B ⏳\n3. GPT-4o - 未配置",
            "continue_skill": False
        }
    
    if action == "test":
        model = params.get("model_name", "deepseek")
        return {
            "skill_id": "model_management",
            "response": f"正在测试 {model} 连接...\n✅ 模型响应正常",
            "continue_skill": False
        }
    
    return {
        "skill_id": "model_management",
        "response": "模型管理支持：配置模型、测试连接、切换模型。请告诉我你要做什么？",
        "continue_skill": True
    }


def handle_strategy_management(action: Optional[str], params: dict, user_message: str) -> dict:
    """策略管理 skill handler"""
    if action == "list":
        return {
            "skill_id": "strategy_management",
            "response": "可用策略：\n1. ATRSTOP - ATR 趋势止损 (推荐)\n2. RSI - RSI 摆动策略\n3. MACD - MACD 趋势策略\n4. BOLLINGER - 布林带均值回归",
            "continue_skill": False
        }
    
    if action == "create":
        strategy_type = params.get("strategy_type", "")
        return {
            "skill_id": "strategy_management",
            "response": f"好的，要创建 {strategy_type} 策略。\n\n请提供策略参数（例如：EMA周期、ATR周期等）",
            "continue_skill": True
        }
    
    return {
        "skill_id": "strategy_management",
        "response": "策略管理支持：创建策略、编辑参数、激活策略。请告诉我你要做什么？",
        "continue_skill": True
    }


def handle_trader_diagnosis(action: Optional[str], params: dict, user_message: str) -> dict:
    """交易员诊断 skill handler"""
    error_msg = params.get("error_message", user_message)
    
    # 基于错误消息模式匹配诊断
    diagnosis_map = {
        "invalid signature": {
            "cause": "API 签名错误，通常是 API Key 或 Secret 配置错误",
            "steps": [
                "1. 检查 API Key 是否正确",
                "2. 检查 API Secret 是否正确",
                "3. 检查 API 权限是否包含交易权限",
                "4. 确认是否使用了正确的网络（主网/测试网）"
            ],
            "severity": "high"
        },
        "insufficient balance": {
            "cause": "账户余额不足",
            "steps": [
                "1. 检查账户 USDT 余额",
                "2. 确认是否有多余的挂单占用保证金",
                "3. 如果是合约交易，确认保证金率"
            ],
            "severity": "medium"
        },
        "connection timeout": {
            "cause": "网络连接超时",
            "steps": [
                "1. 检查网络连接",
                "2. 尝试切换交易所节点",
                "3. 检查防火墙/代理设置"
            ],
            "severity": "low"
        },
        "rate limit": {
            "cause": "请求频率超限",
            "steps": [
                "1. 降低交易频率",
                "2. 等待 1 分钟后重试",
                "3. 考虑使用 WebSocket 获取行情"
            ],
            "severity": "medium"
        }
    }
    
    for error_pattern, diagnosis in diagnosis_map.items():
        if error_pattern.lower() in error_msg.lower():
            return {
                "skill_id": "trader_diagnosis",
                "response": f"🔍 **诊断结果**\n\n**可能原因**: {diagnosis['cause']}\n\n**排查步骤**:\n" + "\n".join(diagnosis["steps"]) + f"\n\n**严重程度**: {diagnosis['severity']}",
                "needs_confirmation": False,
                "continue_skill": False
            }
    
    return {
        "skill_id": "trader_diagnosis",
        "response": f"我收到了这个错误信息：\"{error_msg}\"\n\n为了更好地诊断，请告诉我：\n1. 这个错误是什么时候出现的？\n2. 是一直这样还是偶尔出现？\n3. 你有修改过什么配置吗？",
        "needs_confirmation": False,
        "continue_skill": True
    }


def handle_exchange_diagnosis(action: Optional[str], params: dict, user_message: str) -> dict:
    """交易所连接诊断 skill handler"""
    error_msg = params.get("error_message", user_message)
    
    if "invalid signature" in error_msg.lower():
        return {
            "skill_id": "exchange_diagnosis",
            "response": """🔍 **API 签名错误诊断**

**可能原因**:
1. API Key 或 Secret 填写错误
2. API 权限不足（缺少交易/读取权限）
3. 使用了测试网的 Key 但配置了主网节点

**排查步骤**:
1. 登录交易所网页端，检查 API Key 是否有效
2. 核对 API Key 和 Secret 是否与交易所提供的一致（注意无多余空格）
3. 确认 API Key 的权限勾选了"允许读取"和"允许交易"
4. 如果使用代理，确认代理未篡改请求

**快速修复**:
如果无法解决，建议删除当前 API 配置，重新创建一个新 Key，并确保权限正确。""",
            "needs_confirmation": False,
            "continue_skill": False
        }
    
    return {
        "skill_id": "exchange_diagnosis",
        "response": "请描述具体的错误信息，例如：连接超时、签名错误、拒绝访问等",
        "continue_skill": True
    }


def handle_balance_and_position(action: Optional[str], params: dict, user_message: str) -> dict:
    """余额与持仓查询 skill handler"""
    # TODO: 对接现有 market API
    return {
        "skill_id": "balance_and_position",
        "response": """**账户概览**

**币安 Binance**
- USDT 余额: 1000.00
- BTC 持仓: 0.05 @ 65000 (多仓)
- ETH 持仓: 2.0 @ 3500 (多仓)

**Bybit**
- USDT 余额: 500.00
- 无持仓

**OKX**
- USDT 余额: 0 (未配置)""",
        "needs_confirmation": False,
        "continue_skill": False
    }


def handle_backtest_management(action: Optional[str], params: dict, user_message: str) -> dict:
    """回测管理 skill handler"""
    if action == "run":
        symbol = params.get("symbol", "BTC/USDT")
        strategy = params.get("strategy_type", "RSI")
        return {
            "skill_id": "backtest_management",
            "response": f"好的，正在对 {symbol} 运行 {strategy} 策略回测...\n\n预计需要 30 秒，请稍候。",
            "continue_skill": True
        }
    
    return {
        "skill_id": "backtest_management",
        "response": "回测管理支持：运行回测、查看历史结果。请告诉我你要回测什么策略？",
        "continue_skill": True
    }