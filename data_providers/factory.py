"""
数据源工厂 - 根据市场类型返回对应数据源
参考 QuantDinger 的 app/data_sources/factory.py

使用方式：
  from data_providers import DataProviderFactory
  crypto = DataProviderFactory.get("CRYPTO")
  stock = DataProviderFactory.get("CN_STOCK")
"""

from typing import Dict, Optional
from .base import BaseDataProvider
from .crypto import CryptoDataProvider
from .stock import StockDataProvider
from .us_stock import USStockProvider
from .forex import ForexProvider


class DataProviderFactory:
    """
    数据源工厂

    单例模式管理所有数据源实例，按市场类型路由请求。
    """

    _instances: Dict[str, BaseDataProvider] = {}

    # 市场类型 → 数据源映射
    _PROVIDER_CLASSES = {
        "CRYPTO": CryptoDataProvider,
        "CN_STOCK": StockDataProvider,
        "HK_STOCK": StockDataProvider,
        "US_STOCK": USStockProvider,
        "STOCK": StockDataProvider,
        "FOREX": ForexProvider,
        "FX": ForexProvider,

    }

    @classmethod
    def get(cls, market: str, **kwargs) -> Optional[BaseDataProvider]:
        """
        获取或创建数据源实例

        Args:
            market: 市场类型，如 "CRYPTO", "CN_STOCK", "US_STOCK"
            **kwargs: 传递给数据源构造函数的参数

        Returns:
            BaseDataProvider 实例，不支持的 market 返回 None
        """
        key = market.upper().strip()
        if key in cls._instances:
            return cls._instances[key]

        provider_cls = cls._PROVIDER_CLASSES.get(key)
        if provider_cls is None:
            return None

        instance = provider_cls(**kwargs)
        cls._instances[key] = instance
        return instance

    @classmethod
    def get_or_default(cls, market: str = None) -> BaseDataProvider:
        """
        获取数据源，market 为空时默认返回 CryptoDataProvider
        """
        if market:
            provider = cls.get(market)
            if provider:
                return provider
        return cls.get("CRYPTO")

    @classmethod
    def get_all_status(cls) -> dict:
        """获取所有数据源的状态"""
        return {
            key: inst.get_status()
            for key, inst in cls._instances.items()
        }

    @classmethod
    def reset_all(cls) -> None:
        """重置所有数据源实例（主要用于测试）"""
        cls._instances.clear()

    @classmethod
    def register(cls, market: str, provider_cls: type) -> None:
        """
        注册新的数据源类型

        Args:
            market: 市场类型标识
            provider_cls: 继承自 BaseDataProvider 的类
        """
        cls._PROVIDER_CLASSES[market.upper().strip()] = provider_cls
