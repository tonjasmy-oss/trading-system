"""
Claw402 支付通道

实现 NOFX 的 x402 风格按量付费：
- 用户通过支付通道充值
- 系统按使用量扣费
- 无需用户管理 API Key

这只是一个预留框架，实际接入需要：
1. 配置 Claw402 服务商
2. 对接支付网关
3. 实现计费逻辑
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class PlanType(Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class PricingPlan:
    """计费方案"""
    plan: PlanType
    name: str
    name_zh: str
    monthly_price_usd: float
    features: list[str]
    limits: dict  # e.g. {"traders": 3, "exchanges": 2, "ai_requests": 1000}


@dataclass
class UsageRecord:
    """使用记录"""
    user_id: str
    feature: str
    quantity: float
    cost: float
    timestamp: int


# ─── 定价方案 ─────────────────────────────────────────────────

PRICING_PLANS = {
    PlanType.FREE: PricingPlan(
        plan=PlanType.FREE,
        name="Free",
        name_zh="免费版",
        monthly_price_usd=0,
        features=[
            "1 个交易员",
            "Binance 主网",
            "基础策略",
            "100 次 AI 请求/月"
        ],
        limits={
            "traders": 1,
            "exchanges": 1,
            "ai_requests": 100,
            "backtest_runs": 5
        }
    ),
    PlanType.STARTER: PricingPlan(
        plan=PlanType.STARTER,
        name="Starter",
        name_zh="入门版",
        monthly_price_usd=9.99,
        features=[
            "3 个交易员",
            "Binance + OKX",
            "所有策略",
            "1000 次 AI 请求/月",
            "历史数据"
        ],
        limits={
            "traders": 3,
            "exchanges": 2,
            "ai_requests": 1000,
            "backtest_runs": 50
        }
    ),
    PlanType.PRO: PricingPlan(
        plan=PlanType.PRO,
        name="Pro",
        name_zh="专业版",
        monthly_price_usd=29.99,
        features=[
            "10 个交易员",
            "全部交易所",
            "自定义策略",
            "无限 AI 请求",
            "在线优化",
            "优先支持"
        ],
        limits={
            "traders": 10,
            "exchanges": 5,
            "ai_requests": -1,  # 无限制
            "backtest_runs": -1
        }
    ),
    PlanType.ENTERPRISE: PricingPlan(
        plan=PlanType.ENTERPRISE,
        name="Enterprise",
        name_zh="企业版",
        monthly_price_usd=99.99,
        features=[
            "无限交易员",
            "全部交易所",
            "自定义策略",
            "白标",
            "专属支持",
            "SLA 保障"
        ],
        limits={
            "traders": -1,
            "exchanges": -1,
            "ai_requests": -1,
            "backtest_runs": -1
        }
    ),
}


class Claw402Client:
    """
    Claw402 支付客户端
    
    使用方式：
        client = Claw402Client(api_key="your_claw402_key")
        
        # 检查用户配额
        quota = client.check_quota(user_id="user123", feature="ai_requests")
        if quota["remaining"] > 0:
            # 执行操作
            client.record_usage(user_id="user123", feature="ai_requests", quantity=1)
        else:
            # 提示升级
            raise QuotaExceededError("AI 请求配额已用完，请升级套餐")
    """
    
    def __init__(self, api_key: str = "", base_url: str = "https://claw402.com/api"):
        self.api_key = api_key
        self.base_url = base_url
        self._usage_cache: dict[str, list[UsageRecord]] = {}  # 简化实现，生产应存数据库
    
    def check_quota(self, user_id: str, feature: str, plan: PlanType = PlanType.FREE) -> dict:
        """
        检查用户配额
        
        Returns:
            {
                "feature": str,
                "used": int,
                "limit": int,
                "remaining": int,
                "unlimited": bool
            }
        """
        plan_info = PRICING_PLANS[plan]
        limit = plan_info.limits.get(feature, 0)
        
        used = self._get_usage_count(user_id, feature)
        
        if limit == -1:
            return {
                "feature": feature,
                "used": used,
                "limit": -1,
                "remaining": -1,
                "unlimited": True
            }
        
        return {
            "feature": feature,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "unlimited": False
        }
    
    def record_usage(self, user_id: str, feature: str, quantity: float = 1.0) -> UsageRecord:
        """
        记录使用量
        
        Returns:
            UsageRecord
        """
        record = UsageRecord(
            user_id=user_id,
            feature=feature,
            quantity=quantity,
            cost=self._calculate_cost(feature, quantity),
            timestamp=int(time.time() * 1000)
        )
        
        if user_id not in self._usage_cache:
            self._usage_cache[user_id] = []
        self._usage_cache[user_id].append(record)
        
        logger.info(f"Usage recorded: user={user_id} feature={feature} qty={quantity}")
        return record
    
    def get_current_plan(self, user_id: str) -> PlanType:
        """
        获取用户当前套餐（简化版，生产应从数据库查询）
        """
        # TODO: 从数据库查询用户当前套餐
        return PlanType.FREE
    
    def get_available_plans(self) -> list[PricingPlan]:
        """获取所有可用套餐"""
        return list(PRICING_PLANS.values())
    
    def _get_usage_count(self, user_id: str, feature: str) -> int:
        """获取用户某功能已使用量"""
        if user_id not in self._usage_cache:
            return 0
        
        # 简化：按月统计
        now = int(time.time() * 1000)
        month_start = now - 30 * 24 * 60 * 60 * 1000
        
        return sum(
            int(r.quantity)
            for r in self._usage_cache[user_id]
            if r.feature == feature and r.timestamp >= month_start
        )
    
    def _calculate_cost(self, feature: str, quantity: float) -> float:
        """计算费用（简化版）"""
        # 实际按量计费价格表
        unit_prices = {
            "ai_requests": 0.001,   # $0.001 per request
            "backtest_runs": 0.1,   # $0.1 per backtest
            "traders": 0.0,         # 按月计费，不按量
        }
        return unit_prices.get(feature, 0) * quantity
    
    def create_payment_link(self, plan: PlanType, user_id: str) -> str:
        """
        创建支付链接（预留）
        
        Returns:
            支付页 URL
        """
        # TODO: 接入 x402 或其他支付网关
        return f"https://claw402.com/pay?plan={plan.value}&user={user_id}"
    
    def verify_payment(self, payment_id: str) -> bool:
        """
        验证支付状态（预留）
        """
        # TODO: 接入支付网关验证
        return True


class QuotaExceededError(Exception):
    """配额超限异常"""
    def __init__(self, message: str, feature: str, limit: int):
        self.feature = feature
        self.limit = limit
        super().__init__(message)


# ─── 装饰器：自动检查配额 ──────────────────────────────────────

def check_quota(feature: str, quantity: float = 1):
    """
    配额检查装饰器
    
    用法：
        @check_quota("ai_requests")
        def call_ai(message: str) -> str:
            return ai_client.chat(message)
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # 从上下文获取 user_id（简化，生产应从 request context 获取）
            user_id = kwargs.get("user_id", "default_user")
            
            from agent.config import PAYMENT_ENABLED
            if not PAYMENT_ENABLED:
                return func(*args, **kwargs)
            
            client = Claw402Client()
            quota = client.check_quota(user_id, feature)
            
            if not quota["unlimited"] and quota["remaining"] < quantity:
                raise QuotaExceededError(
                    f"配额不足：{feature} 剩余 {quota['remaining']}，需要 {quantity}",
                    feature,
                    quota["limit"]
                )
            
            result = func(*args, **kwargs)
            
            # 记录使用量
            client.record_usage(user_id, feature, quantity)
            
            return result
        return wrapper
    return decorator