"""
correlation_guard.py — 相关性感知组合管理
============================================

防止高相关标的同时开仓，降低组合集中风险。

规则：
  - BTC+ETH 相关系数 ~0.85 → 同时开仓需要更高的独立信号强度
  - 同类标的（Layer1: SOL/AVAX/SUI, L2: ARB/OP）→ 最多选 1 个
  - 总开仓数不超过 4 个（其中 L1 最多 2 个）

使用方式：
  guard = CorrelationGuard()
  allowed = guard.can_open("SOL/USDT", current_positions={"BTC/USDT","ETH/USDT"})
"""

CORRELATION_GROUPS = {
    "mega_cap":  {"BTC/USDT", "ETH/USDT"},
    "layer1":    {"SOL/USDT", "AVAX/USDT", "SUI/USDT", "NEAR/USDT"},
    "layer2":    {"ARB/USDT", "OP/USDT", "MATIC/USDT", "BASE/USDT"},
    "defi":      {"LINK/USDT", "UNI/USDT", "AAVE/USDT", "MKR/USDT"},
}

# 跨组相关系数矩阵（简化，实际应从历史数据计算）
CORRELATION_MATRIX = {
    ("BTC/USDT", "ETH/USDT"): 0.85,
    ("BTC/USDT", "SOL/USDT"): 0.70,
    ("ETH/USDT", "SOL/USDT"): 0.72,
    ("SOL/USDT", "AVAX/USDT"): 0.78,
    ("SOL/USDT", "SUI/USDT"): 0.65,
    ("ARB/USDT", "OP/USDT"): 0.80,
}

MAX_POSITIONS_TOTAL = 4
MAX_PER_GROUP = 2
MAX_CORRELATION_EXPOSURE = 0.80  # 当相关度超过此值，拒绝同组新仓


class CorrelationGuard:
    """相关性风控"""

    def __init__(self):
        pass

    def get_group(self, symbol: str) -> str:
        for group_name, members in CORRELATION_GROUPS.items():
            if symbol in members:
                return group_name
        return "other"

    def can_open(self, symbol: str, current_positions: set) -> tuple:
        """
        检查是否可以开仓

        Returns:
            (allowed: bool, reason: str)
        """
        # 总仓位限制
        if len(current_positions) >= MAX_POSITIONS_TOTAL:
            return False, f"总仓位已达上限 {MAX_POSITIONS_TOTAL}"

        group = self.get_group(symbol)
        same_group = {p for p in current_positions if self.get_group(p) == group}

        # 同组限制
        if len(same_group) >= MAX_PER_GROUP:
            return False, f"同组仓位已达上限 {MAX_PER_GROUP}（{group}）"

        # 高相关检查
        for existing in current_positions:
            corr = self._get_correlation(symbol, existing)
            if corr >= MAX_CORRELATION_EXPOSURE:
                return False, f"与 {existing} 相关度过高 ({corr:.0%})"
            if corr >= 0.6 and len(same_group) >= 1:
                return False, f"与 {existing} 中高相关 ({corr:.0%}) + 同组已有仓位"

        return True, "OK"

    def _get_correlation(self, a: str, b: str) -> float:
        key = (a, b) if (a, b) in CORRELATION_MATRIX else (b, a)
        return CORRELATION_MATRIX.get(key, 0.3)

    def suggest_diversify(self, symbol: str, current_positions: set) -> list:
        """建议：如果要开仓 symbol，哪些已有仓位应考虑平仓"""
        suggestions = []
        for existing in current_positions:
            corr = self._get_correlation(symbol, existing)
            if corr >= 0.7:
                suggestions.append({
                    "symbol": existing,
                    "correlation": corr,
                    "action": "consider_close",
                    "reason": f"与 {symbol} 高度相关 ({corr:.0%})"
                })
        return suggestions
