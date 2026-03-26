"""
CoE 模块（Center of Excellence）

自动化卓越中心模块包

主要组件：
- asset_registry: 自动化资产注册中心
- api_routes: 资产库 REST API

使用示例：
    from coe.asset_registry import AssetRegistry, get_registry
    from coe.api_routes import setup_asset_routes

    # 获取全局单例
    registry = get_registry()

    # 注册一个 RPA Bot
    bot = registry.register_asset(
        name="Web Scraper Bot",
        asset_type="virtual_device",
        metadata={"owner": "alice", "team": "iot", "tags": ["rpa", "scraper"]},
        bot_type="playwright",
        entry_point="rpa_tasks.web_scraper.scrape",
        capabilities=["web_scrape", "form_fill"],
    )

    # 查询资产
    bots = registry.list_by_type("virtual_device")
    print(f"共有 {len(bots)} 个虚拟设备资产")

    # 更新状态
    registry.advance_lifecycle(bot.id, "testing")
"""

from .asset_registry import (
    AssetRegistry,
    Asset,
    PhysicalDeviceAsset,
    VirtualDeviceAsset,
    BpmProcessAsset,
    AiSkillAsset,
    ScriptAsset,
    AssetMetadata,
    AssetType,
    AssetStatus,
    AssetIdGenerator,
    get_registry,
)

from .api_routes import setup_asset_routes

__all__ = [
    "AssetRegistry",
    "Asset",
    "PhysicalDeviceAsset",
    "VirtualDeviceAsset",
    "BpmProcessAsset",
    "AiSkillAsset",
    "ScriptAsset",
    "AssetMetadata",
    "AssetType",
    "AssetStatus",
    "AssetIdGenerator",
    "get_registry",
    "setup_asset_routes",
]
