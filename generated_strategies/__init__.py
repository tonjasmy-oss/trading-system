"""
AI 生成策略注册中心

SignalEngine 通过 REGISTRY 自动发现所有生成策略。
每个生成策略文件必须包含：
  - 一个继承 BaseStrategy 的类
  - 类名需以 Strategy 结尾
  - 实现 compute(self, candles) -> (int, float, float)

生成策略通过以下方式被 SignalEngine 识别：
  AGENT_SYMBOLS=ETH/USDT:GEN_MyRSI:weex
"""

import os
import importlib
import logging
from typing import Dict, Type

logger = logging.getLogger(__name__)

REGISTRY: Dict[str, Type] = {}


def discover():
    """扫描 generated_strategies/ 目录，自动注册所有生成策略"""
    global REGISTRY
    REGISTRY.clear()

    base_dir = os.path.dirname(__file__)
    for fname in sorted(os.listdir(base_dir)):
        if fname.startswith("_") or fname.startswith("."):
            continue
        if not fname.endswith(".py"):
            continue

        module_name = fname[:-3]
        try:
            mod = importlib.import_module(f"generated_strategies.{module_name}")
            from components.signal_engine import BaseStrategy
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseStrategy)
                    and obj is not BaseStrategy
                ):
                    key = f"GEN_{obj.__name__}"
                    REGISTRY[key] = obj
                    logger.info(f"[generated] 注册策略: {key}")
                    break
        except Exception as e:
            logger.warning(f"[generated] 加载 {module_name} 失败: {e}")


def get_strategy_class(name: str):
    """根据名称获取策略类。支持多种名称格式：
       - 'GEN_TestEmaCross' → 查找 REGISTRY['GEN_TestEmaCrossStrategy']
       - 'TestEmaCrossStrategy' → 同上
       - 'GEN_test_ema_cross' → 同上
    """
    # 精确匹配
    if name in REGISTRY:
        return REGISTRY[name]

    # 去掉 GEN_ 前缀后模糊匹配
    clean = name.upper().replace("GEN_", "").replace("STRATEGY", "")
    for key, cls in REGISTRY.items():
        key_clean = key.upper().replace("GEN_", "").replace("STRATEGY", "")
        if clean == key_clean:
            return cls
    return None


def list_generated() -> Dict[str, str]:
    """列出所有生成策略"""
    return {k: f"{v.__module__}.{v.__name__}" for k, v in REGISTRY.items()}


discover()
