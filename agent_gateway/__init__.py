"""
Agent Gateway - AI 客户端对接网关
参考 QuantDinger 的 agent_v1 设计

提供：
  - Token 签发与验证
  - Scope 权限控制（R/W/B/T）
  - 审计日志
  - FastAPI 路由
"""

from .routes import create_agent_router

__all__ = ["create_agent_router"]
