"""
自动化资产库（Automation Asset Library）

管理项目中所有自动化相关的资产：
- 物理设备（physical_device）
- 虚拟设备/RPA Bot（virtual_device）
- BPM 流程（bpm_process）
- AI 技能（ai_skill）
- 脚本（script）

使用方法：
    from coe.asset_registry import AssetRegistry
    registry = AssetRegistry()
    registry.register_asset(...)
    registry.list_assets()
    registry.get_asset("bot-001")
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Literal
from dataclasses import dataclass, field, asdict
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)

AssetType = Literal["physical_device", "virtual_device", "bpm_process", "ai_skill", "script"]
AssetStatus = Literal["planning", "development", "testing", "staging", "production", "deprecated", "archived"]
LifecyclePhase = Literal["discovery", "development", "testing", "staging", "production", "deprecation", "archived"]

# ID 前缀映射：asset_type → 2字符前缀
_ID_PREFIX = {
    "physical_device": "PD",
    "virtual_device":  "VD",
    "bpm_process":    "BP",
    "ai_skill":       "SK",
    "script":         "SC",
}


class AssetIdGenerator:
    """
    顺序 ID 生成器

    ID 格式：{前缀}-{3位顺序号}
    例如：VD-001, PD-023, BP-007

    计数器存储在 asset_registry.json 的 counters 字段中，
    每次生成后自增，不复用。
    """

    def __init__(self, registry_file: Path):
        self.registry_file = registry_file
        self._counters: dict[str, int] = {}  # prefix → last number
        self._load_counters()

    def _load_counters(self):
        if self.registry_file.exists():
            try:
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._counters = data.get("counters", {})
            except Exception:
                self._counters = {}

    def _save_counters(self):
        if not self.registry_file.exists():
            return
        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data["counters"] = self._counters
        with open(self.registry_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def next(self, asset_type: AssetType) -> str:
        """生成下一个指定类型的资产 ID"""
        prefix = _ID_PREFIX.get(asset_type, "XX")
        current = self._counters.get(prefix, 0)
        new_num = current + 1
        self._counters[prefix] = new_num
        self._save_counters()
        asset_id = f"{prefix}-{new_num:03d}"
        logger.debug("asset_id_generated", asset_type=asset_type, asset_id=asset_id)
        return asset_id

    def get_current(self, asset_type: AssetType) -> int:
        """获取当前指定类型的最大序号（未使用的下一个）"""
        prefix = _ID_PREFIX.get(asset_type, "XX")
        return self._counters.get(prefix, 0)

    def reset(self, asset_type: AssetType, to: int = 0):
        """重置计数器（谨慎使用）"""
        prefix = _ID_PREFIX.get(asset_type, "XX")
        self._counters[prefix] = to
        self._save_counters()
        logger.warning("asset_counter_reset", prefix=prefix, to=to)


class AssetEncoder(json.JSONEncoder):
    """支持 datetime 序列化的 JSON 编码器"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


# ============================================================
# Asset 模型定义
# ============================================================

@dataclass
class AssetMetadata:
    """资产元数据"""
    owner: str = "unknown"           # 负责人
    team: str = "general"            # 所属团队
    tags: list[str] = field(default_factory=list)   # 标签
    description: str = ""            # 资产描述
    version: str = "1.0.0"           # 当前版本
    created_at: str = ""              # 创建时间
    updated_at: str = ""              # 更新时间
    documentation: str = ""           # 文档路径

    def to_dict(self) -> dict:
        return {
            "owner": self.owner,
            "team": self.team,
            "tags": self.tags,
            "description": self.description,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "documentation": self.documentation,
        }


@dataclass
class Asset:
    """通用自动化资产"""
    id: str                           # 资产唯一标识
    name: str                         # 资产名称（人类可读）
    type: AssetType                   # 资产类型
    status: AssetStatus = "planning"  # 当前状态
    metadata: AssetMetadata = field(default_factory=AssetMetadata)
    runtime_state: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        metadata = self.metadata.to_dict() if isinstance(self.metadata, AssetMetadata) else self.metadata
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "metadata": metadata,
            "runtime_state": self.runtime_state,
        }


@dataclass
class PhysicalDeviceAsset(Asset):
    """物理设备资产"""
    type: AssetType = "physical_device"
    hardware_info: dict = field(default_factory=dict)
    protocol: str = "ws"

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["hardware_info"] = self.hardware_info
        base["protocol"] = self.protocol
        return base


@dataclass
class VirtualDeviceAsset(Asset):
    """虚拟设备 / RPA Bot 资产"""
    type: AssetType = "virtual_device"
    bot_type: str = "playwright"
    entry_point: str = ""
    capabilities: list[str] = field(default_factory=list)
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "bot_type": self.bot_type,
            "entry_point": self.entry_point,
            "capabilities": self.capabilities,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        })
        return base


@dataclass
class BpmProcessAsset(Asset):
    """BPM 流程资产"""
    type: AssetType = "bpm_process"
    process_definition_path: str = ""
    version: str = "1.0.0"
    active_instances: int = 0
    total_instances: int = 0
    avg_duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "process_definition_path": self.process_definition_path,
            "version": self.version,
            "active_instances": self.active_instances,
            "total_instances": self.total_instances,
            "avg_duration_seconds": self.avg_duration_seconds,
        })
        return base


@dataclass
class AiSkillAsset(Asset):
    """AI 技能资产"""
    type: AssetType = "ai_skill"
    model_provider: str = "openai"
    model_name: str = "gpt-4o"
    input_type: str = "text"
    output_type: str = "text"
    capabilities: list[str] = field(default_factory=list)
    call_count: int = 0
    success_rate: float = 1.0

    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "input_type": self.input_type,
            "output_type": self.output_type,
            "capabilities": self.capabilities,
            "call_count": self.call_count,
            "success_rate": self.success_rate,
        })
        return base


@dataclass
class ScriptAsset(Asset):
    """脚本资产（对应现有 script_runner.py）"""
    type: AssetType = "script"
    script_path: str = ""
    language: str = "javascript"
    parameters: list[dict] = field(default_factory=list)
    run_count: int = 0
    avg_duration_ms: float = 0.0

    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "script_path": self.script_path,
            "language": self.language,
            "parameters": self.parameters,
            "run_count": self.run_count,
            "avg_duration_ms": self.avg_duration_ms,
        })
        return base


# ============================================================
# 资产注册表
# ============================================================

class AssetRegistry:
    """
    自动化资产注册中心

    提供资产的注册、查询、更新、删除、健康检查等能力。
    持久化到 server/coe/asset_registry.json
    """

    ASSET_FILE = Path(__file__).parent / "asset_registry.json"

    def __init__(self, load: bool = True):
        self._assets: dict[str, Asset] = {}
        self._id_gen = AssetIdGenerator(self.ASSET_FILE)
        if load:
            self._load()
        logger.info("asset_registry_initialized", assets_count=len(self._assets))

    # ---- 持久化 ----

    def _load(self):
        """从 JSON 文件加载资产注册表"""
        if not self.ASSET_FILE.exists():
            return
        try:
            with open(self.ASSET_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("assets", []):
                asset = self._dict_to_asset(item)
                if asset:
                    self._assets[asset.id] = asset
            logger.info("asset_registry_loaded", count=len(self._assets))
        except Exception as e:
            logger.error("asset_registry_load_failed", error=str(e))

    def _save(self):
        """保存资产注册表到文件"""
        try:
            self.ASSET_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": "1.0",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "assets": [asset.to_dict() for asset in self._assets.values()]
            }
            with open(self.ASSET_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, cls=AssetEncoder)
            logger.debug("asset_registry_saved", count=len(self._assets))
        except Exception as e:
            logger.error("asset_registry_save_failed", error=str(e))

    def _dict_to_asset(self, d: dict) -> Optional[Asset]:
        """根据 type 字段将 dict 反序列化为正确的 Asset 子类"""
        asset_type = d.get("type")
        runtime_state = d.pop("runtime_state", None)
        try:
            # 反序列化 metadata
            if "metadata" in d and isinstance(d["metadata"], dict):
                d["metadata"] = AssetMetadata(**d["metadata"])

            if asset_type == "physical_device":
                asset = PhysicalDeviceAsset(**d)
            elif asset_type == "virtual_device":
                asset = VirtualDeviceAsset(**d)
            elif asset_type == "bpm_process":
                asset = BpmProcessAsset(**d)
            elif asset_type == "ai_skill":
                asset = AiSkillAsset(**d)
            elif asset_type == "script":
                asset = ScriptAsset(**d)
            else:
                asset = Asset(**d)

            if runtime_state:
                asset.runtime_state = runtime_state
            return asset
        except Exception as e:
            logger.warning("asset_deserialization_failed", d=d, error=str(e))
            return None

    # ---- 注册 / 注销 ----

    def register_asset(
        self,
        name: str,
        asset_type: AssetType,
        metadata: Optional[AssetMetadata] = None,
        **kwargs
    ) -> Asset:
        """注册一个新资产，返回创建的 Asset 对象
        
        Args:
            name: 资产名称
            asset_type: 资产类型
            metadata: 元数据（可选）
            **kwargs: 传给具体 Asset 子类的额外字段
        """
        now = datetime.now(timezone.utc).isoformat()
        if metadata is None:
            metadata = AssetMetadata(created_at=now, updated_at=now)
        elif isinstance(metadata, dict):
            metadata = AssetMetadata(**{**metadata, "created_at": now, "updated_at": now})
        elif isinstance(metadata, AssetMetadata):
            metadata.created_at = now
            metadata.updated_at = now

        asset_id = self._id_gen.next(asset_type)

        # 按 type 创建对应的子类实例
        if asset_type == "physical_device":
            asset = PhysicalDeviceAsset(
                id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs
            )
        elif asset_type == "virtual_device":
            asset = VirtualDeviceAsset(
                id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs
            )
        elif asset_type == "bpm_process":
            asset = BpmProcessAsset(
                id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs
            )
        elif asset_type == "ai_skill":
            asset = AiSkillAsset(
                id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs
            )
        elif asset_type == "script":
            asset = ScriptAsset(
                id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs
            )
        else:
            asset = Asset(
                id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs
            )

        self._assets[asset.id] = asset
        self._save()
        logger.info("asset_registered", asset_id=asset.id, name=name, type=asset_type)
        return asset

    def unregister_asset(self, asset_id: str) -> bool:
        """注销一个资产（软删除，标记为 archived）"""
        asset = self._assets.get(asset_id)
        if not asset:
            logger.warning("asset_not_found", asset_id=asset_id)
            return False
        asset.status = "archived"
        asset.metadata.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info("asset_archived", asset_id=asset_id)
        return True

    # ---- 查询 ----

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        """通过 ID 获取资产"""
        return self._assets.get(asset_id)

    def list_assets(
        self,
        asset_type: Optional[AssetType] = None,
        status: Optional[AssetStatus] = None,
        owner: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> list[Asset]:
        """按条件筛选资产"""
        results = list(self._assets.values())
        if asset_type:
            results = [a for a in results if a.type == asset_type]
        if status:
            results = [a for a in results if a.status == status]
        if owner:
            results = [a for a in results if a.metadata.owner == owner]
        if tag:
            results = [a for a in results if tag in a.metadata.tags]
        return results

    def list_by_type(self, asset_type: AssetType) -> list[Asset]:
        """按类型列出所有资产（快捷方法）"""
        return self.list_assets(asset_type=asset_type)

    # ---- 更新 ----

    def update_status(self, asset_id: str, status: AssetStatus) -> bool:
        """更新资产状态（生命周期推进）"""
        asset = self._assets.get(asset_id)
        if not asset:
            return False
        old_status = asset.status
        asset.status = status
        asset.metadata.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info("asset_status_updated", asset_id=asset_id, old=old_status, new=status)
        return True

    def update_runtime_state(self, asset_id: str, state: dict) -> bool:
        """更新运行时状态（内存，非持久化）"""
        asset = self._assets.get(asset_id)
        if not asset:
            return False
        asset.runtime_state.update(state)
        return True

    def advance_lifecycle(self, asset_id: str, target_phase: LifecyclePhase) -> bool:
        """推进资产生命周期阶段
        
        生命周期: planning → development → testing → staging → production → deprecation → archived
        """
        phase_order = [
            "planning", "development", "testing", "staging",
            "production", "deprecation", "archived"
        ]

        status_map = {
            "discovery": "planning",
            "development": "development",
            "testing": "testing",
            "staging": "staging",
            "production": "production",
            "deprecation": "deprecated",
            "archived": "archived",
        }

        if target_phase not in phase_order:
            logger.error("invalid_lifecycle_phase", phase=target_phase)
            return False

        new_status = status_map.get(target_phase, target_phase)
        return self.update_status(asset_id, new_status)

    # ---- 统计 ----

    def summary(self) -> dict:
        """资产统计摘要"""
        total = len(self._assets)
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for asset in self._assets.values():
            by_type[asset.type] = by_type.get(asset.type, 0) + 1
            by_status[asset.status] = by_status.get(asset.status, 0) + 1
        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
        }

    # ---- 与 device_manager 同步 ----

    def sync_from_device_manager(self, devices: dict) -> int:
        """
        从 device_manager 的 devices Map 同步物理设备资产

        Args:
            devices: device_manager.devices（dict of {id: state_dict})
        Returns:
            同步的设备数量
        """
        synced = 0
        for dev_id, state in devices.items():
            if any(a.id == dev_id for a in self._assets.values()):
                # 已存在，更新运行时状态
                asset = next(a for a in self._assets.values() if a.id == dev_id)
                asset.runtime_state = state
            else:
                # 新设备，注册为物理资产
                device_type = state.get("type", "generic")
                self.register_asset(
                    name=f"Physical {dev_id}",
                    asset_type="physical_device",
                    metadata=AssetMetadata(
                        owner=state.get("owner", "system"),
                        description=f"Auto-synced device from device_manager: {dev_id}",
                        tags=["auto-synced", device_type],
                    ),
                )
                synced += 1
        if synced > 0:
            self._save()
        return synced


# ============================================================
# 资产注册表实例（全局单例）
# ============================================================
_global_registry: Optional[AssetRegistry] = None


def get_registry() -> AssetRegistry:
    """获取全局资产注册表单例"""
    global _global_registry
    if _global_registry is None:
        _global_registry = AssetRegistry()
    return _global_registry
