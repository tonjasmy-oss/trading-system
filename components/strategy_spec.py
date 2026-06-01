"""
strategy_spec.py — StrategySpec JSON 策略规格
==============================================

借鉴 QuantDinger 的 StrategySpec 设计：
  - LLM/AI 可以用 JSON 描述策略
  - 系统自动编译为可执行 Python 策略类
  - 支持验证和归一化

格式示例：
  {
    "name": "rsi_oversold_bounce",
    "version": "1.0",
    "description": "RSI 超卖反弹策略",
    "risk": {"stop_loss": 0.02, "take_profit": 0.04},
    "indicators": [{"name": "rsi", "type": "RSI", "params": {"period": 14}}],
    "entry_conditions": [
      {"indicator": "rsi", "operator": "cross_above", "value": 28}
    ],
    "exit_conditions": [
      {"indicator": "rsi", "operator": "cross_below", "value": 65}
    ],
    "params": {
      "rsi_period": {"default": 14, "min": 8, "max": 30},
      "oversold": {"default": 28, "min": 15, "max": 40}
    }
  }
"""

import json
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 支持的指标类型
SUPPORTED_INDICATORS = {
    "RSI": {"params": ["period"], "defaults": {"period": 14}},
    "EMA": {"params": ["period"], "defaults": {"period": 20}},
    "SMA": {"params": ["period"], "defaults": {"period": 20}},
    "BOLLINGER": {"params": ["period", "std_dev"], "defaults": {"period": 20, "std_dev": 2.0}},
    "MACD": {"params": ["fast", "slow", "signal"], "defaults": {"fast": 12, "slow": 26, "signal": 9}},
    "ATR": {"params": ["period"], "defaults": {"period": 14}},
    "DONCHIAN": {"params": ["period"], "defaults": {"period": 20}},
}

# 支持的信号操作符
SUPPORTED_OPERATORS = {
    "cross_above": "Ind_A crosses above value V",
    "cross_below": "Ind_A crosses below value V",
    "gt": "Ind_A > value V",
    "lt": "Ind_A < value V",
    "gte": "Ind_A >= value V",
    "lte": "Ind_A <= value V",
    "cross_above_ind": "Ind_A crosses above Ind_B",
    "cross_below_ind": "Ind_A crosses below Ind_B",
    "gt_ind": "Ind_A > Ind_B",
    "lt_ind": "Ind_A < Ind_B",
}


@dataclass
class StrategySpec:
    """策略规格 — AI 可读/可写的 JSON 中间表示"""

    name: str
    version: str = "1.0"
    description: str = ""

    # 风险配置
    risk: Dict[str, Any] = field(default_factory=dict)

    # 指标定义
    indicators: List[Dict[str, Any]] = field(default_factory=list)

    # 入场条件
    entry_conditions: List[Dict[str, Any]] = field(default_factory=list)

    # 出场条件
    exit_conditions: List[Dict[str, Any]] = field(default_factory=list)

    # 可调参数
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 适合的市场状态
    suitable_regimes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "risk": self.risk,
            "indicators": self.indicators,
            "entry_conditions": self.entry_conditions,
            "exit_conditions": self.exit_conditions,
            "params": self.params,
            "suitable_regimes": self.suitable_regimes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategySpec":
        return cls(
            name=d.get("name", "unnamed"),
            version=d.get("version", "1.0"),
            description=d.get("description", ""),
            risk=d.get("risk", {}),
            indicators=d.get("indicators", []),
            entry_conditions=d.get("entry_conditions", []),
            exit_conditions=d.get("exit_conditions", []),
            params=d.get("params", {}),
            suitable_regimes=d.get("suitable_regimes", []),
        )

    @classmethod
    def from_json(cls, s: str) -> "StrategySpec":
        return cls.from_dict(json.loads(s))

    def validate(self) -> List[str]:
        """
        验证策略规格的合法性

        Returns:
            错误列表，空列表 = 有效
        """
        errors = []

        if not self.name:
            errors.append("name is required")
        if not self.indicators:
            errors.append("at least one indicator is required")
        if not self.entry_conditions and not self.exit_conditions:
            errors.append("at least one entry or exit condition is required")

        # 验证指标
        indicator_names = set()
        for ind in self.indicators:
            name = ind.get("name", "")
            if not name:
                errors.append("indicator name is required")
                continue
            if name in indicator_names:
                errors.append(f"duplicate indicator name: {name}")
            indicator_names.add(name)

            ind_type = ind.get("type", "")
            if ind_type not in SUPPORTED_INDICATORS:
                errors.append(f"unsupported indicator type: {ind_type}")
            else:
                spec = SUPPORTED_INDICATORS[ind_type]
                params = ind.get("params", {})
                for p in spec["params"]:
                    if p not in params:
                        params[p] = spec["defaults"].get(p, 0)

        # 验证条件
        for cond in self.entry_conditions + self.exit_conditions:
            op = cond.get("operator", "")
            if op not in SUPPORTED_OPERATORS:
                errors.append(f"unsupported operator: {op}")

            ind = cond.get("indicator", "")
            if ind and ind not in indicator_names:
                errors.append(f"condition references unknown indicator: {ind}")

            # cross_above_ind / cross_below_ind 需要 indicator_b
            if op in ("cross_above_ind", "cross_below_ind", "gt_ind", "lt_ind"):
                if not cond.get("indicator_b"):
                    errors.append(f"operator {op} requires 'indicator_b'")

        # 验证参数
        for pname, pdef in self.params.items():
            if "default" not in pdef:
                errors.append(f"param {pname} missing 'default'")

        return errors

    def generate_python_class(self, class_name: str = None) -> str:
        """
        编译为可执行的 Python 策略类代码

        Returns:
            完整的 Python 类源码字符串
        """
        name = class_name or f"{self.name.title().replace('_', '')}Strategy"

        # 构建 risk_config
        risk_lines = []
        for k, v in self.risk.items():
            risk_lines.append(f"        {k}={v!r},")
        risk_block = "\n".join(risk_lines)

        # 构建指标计算代码
        indicator_code_lines = []
        for ind in self.indicators:
            ind_name = ind["name"]
            ind_type = ind["type"]
            params = ind.get("params", {})
            if ind_type == "RSI":
                period = params.get("period", 14)
                indicator_code_lines.append(
                    f'        indicators["{ind_name}"] = self.RSI(closes, {period})'
                )
            elif ind_type == "EMA":
                period = params.get("period", 20)
                indicator_code_lines.append(
                    f'        indicators["{ind_name}"] = self.EMA(closes, {period})'
                )
            elif ind_type == "SMA":
                period = params.get("period", 20)
                indicator_code_lines.append(
                    f'        indicators["{ind_name}"] = self.SMA(closes, {period})'
                )
            else:
                indicator_code_lines.append(
                    f'        # TODO: implement {ind_type} indicator for "{ind_name}"'
                )
        indicator_block = "\n".join(indicator_code_lines)

        # 构建信号条件代码
        def _compile_condition(cond: dict) -> str:
            op = cond["operator"]
            ind = cond.get("indicator", "")
            val = cond.get("value", 0)
            ind_b = cond.get("indicator_b", "")

            if op == "cross_above":
                return f'indicators["{ind}"][i-1] <= {val} and indicators["{ind}"][i] > {val}'
            elif op == "cross_below":
                return f'indicators["{ind}"][i-1] >= {val} and indicators["{ind}"][i] < {val}'
            elif op == "gt":
                return f'indicators["{ind}"][i] > {val}'
            elif op == "lt":
                return f'indicators["{ind}"][i] < {val}'
            elif op == "gte":
                return f'indicators["{ind}"][i] >= {val}'
            elif op == "lte":
                return f'indicators["{ind}"][i] <= {val}'
            elif op == "cross_above_ind":
                return f'indicators["{ind}"][i-1] <= indicators["{ind_b}"][i-1] and indicators["{ind}"][i] > indicators["{ind_b}"][i]'
            elif op == "cross_below_ind":
                return f'indicators["{ind}"][i-1] >= indicators["{ind_b}"][i-1] and indicators["{ind}"][i] < indicators["{ind_b}"][i]'
            elif op == "gt_ind":
                return f'indicators["{ind}"][i] > indicators["{ind_b}"][i]'
            elif op == "lt_ind":
                return f'indicators["{ind}"][i] < indicators["{ind_b}"][i]'
            return "False"

        entry_code = " or ".join(f"({_compile_condition(c)})" for c in self.entry_conditions) or "False"
        exit_code = " or ".join(f"({_compile_condition(c)})" for c in self.exit_conditions) or "False"

        # 参数注入
        param_defaults = []
        pnames = list(self.params.keys())
        for idx, (pname, pdef) in enumerate(self.params.items()):
            comma = "," if idx < len(pnames) - 1 else ""
            param_defaults.append(f"        {pname}={pdef['default']!r}{comma}")
        param_block = "\n".join(param_defaults) if param_defaults else "        pass"
        param_comma = ", " if param_defaults else ""

        lines = []
        lines.append(f'"""')
        lines.append(f'{self.description or self.name} — AI Generated Strategy')
        lines.append(f'Version: {self.version}')
        lines.append(f'Generated by StrategySpec compiler')
        lines.append(f'"""')
        lines.append(f'')
        lines.append(f'from components.layered_strategy import LayeredStrategy, StrategyRisk')
        lines.append(f'from strategies import Signal')
        lines.append(f'')
        lines.append(f'')
        lines.append(f'class {name}(LayeredStrategy):')
        lines.append(f'    """{self.description or self.name}"""')
        lines.append(f'')
        lines.append(f'    risk_config = StrategyRisk(')
        lines.append(risk_block)
        lines.append(f'        name="{self.name}",')
        lines.append(f'        description="{self.description}",')
        lines.append(f'        suitable_regimes={self.suitable_regimes!r},')
        lines.append(f'    )')
        lines.append(f'')
        lines.append(f'    def __init__(self, config=None{param_comma}')
        lines.append(f'{param_block}):')
        lines.append(f'        super().__init__(config)')
        for pname in self.params:
            lines.append(f'        self.{pname} = {pname}')
        lines.append(f'')
        lines.append(f'    def compute_indicators(self, candles):')
        lines.append(f'        closes = [c["close"] for c in candles]')
        lines.append(f'        n = len(candles)')
        lines.append(f'        indicators = {{}}')
        lines.append(indicator_block)
        lines.append(f'        return indicators')
        lines.append(f'')
        lines.append(f'    def generate_signals(self, candles, indicators):')
        lines.append(f'        n = len(candles)')
        lines.append(f'        entry = [Signal.HOLD] * n')
        lines.append(f'        exit_ = [Signal.HOLD] * n')
        lines.append(f'')
        lines.append(f'        for i in range(1, n):')
        lines.append(f'            if any(indicators.get(k, [0])[i] == 0 for k in indicators):')
        lines.append(f'                continue')
        lines.append(f'')
        lines.append(f'            if {entry_code}:')
        lines.append(f'                entry[i] = Signal.BUY')
        lines.append(f'            elif {exit_code}:')
        lines.append(f'                exit_[i] = Signal.SELL')
        lines.append(f'')
        lines.append(f'        return entry, exit_')
        return "\n".join(lines)


def validate_spec_json(spec_json: str) -> List[str]:
    """验证 JSON 策略规格"""
    try:
        spec = StrategySpec.from_json(spec_json)
        return spec.validate()
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]
    except Exception as e:
        return [f"Validation error: {e}"]


# ============================================================
# 预置示例
# ============================================================

EXAMPLE_RSI_SPEC = {
    "name": "rsi_oversold_bounce",
    "version": "1.0",
    "description": "RSI 超卖反弹策略",
    "risk": {
        "stop_loss": 0.02,
        "take_profit": 0.04,
        "capital_pct": 1.0,
        "trade_direction": "long"
    },
    "indicators": [
        {"name": "rsi", "type": "RSI", "params": {"period": 14}}
    ],
    "entry_conditions": [
        {"indicator": "rsi", "operator": "cross_above", "value": 28}
    ],
    "exit_conditions": [
        {"indicator": "rsi", "operator": "cross_below", "value": 65}
    ],
    "params": {
        "rsi_period": {"default": 14, "min": 8, "max": 30},
        "oversold": {"default": 28, "min": 15, "max": 40},
        "overbought": {"default": 65, "min": 55, "max": 80}
    },
    "suitable_regimes": ["downtrend", "ranging"]
}

EXAMPLE_EMA_CROSS_SPEC = {
    "name": "ema_cross",
    "version": "1.0",
    "description": "EMA 金叉死叉策略",
    "risk": {
        "stop_loss": 0.025,
        "take_profit": 0.05,
        "capital_pct": 1.0,
        "trade_direction": "long"
    },
    "indicators": [
        {"name": "ema_fast", "type": "EMA", "params": {"period": 10}},
        {"name": "ema_slow", "type": "EMA", "params": {"period": 30}}
    ],
    "entry_conditions": [
        {"indicator": "ema_fast", "operator": "cross_above_ind", "indicator_b": "ema_slow"}
    ],
    "exit_conditions": [
        {"indicator": "ema_fast", "operator": "cross_below_ind", "indicator_b": "ema_slow"}
    ],
    "params": {
        "fast_period": {"default": 10, "min": 5, "max": 30},
        "slow_period": {"default": 30, "min": 15, "max": 100}
    },
    "suitable_regimes": ["uptrend", "downtrend"]
}
