"""
自动化资产库（Automation Asset Library）

管理项目中所有自动化相关的资产：
- 设备（device，含 physical / virtual 两种子类型）
- BPM 流程（bpm_process）
- AI 技能（ai_skill）
- 脚本（script）

使用方法：
    from coe.asset_registry import AssetRegistry, get_registry, AssetMetadata

    registry = get_registry()
    registry.register_asset(
        asset_id="temp-sensor-01",
        name="温度传感器 01",
        asset_type="device",
        device_subtype="physical",
        metadata=AssetMetadata(owner="alice", tags=["sensor"]),
    )
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Literal
from dataclasses import dataclass, field, asdict

import structlog

logger = structlog.get_logger(__name__)

AssetType = Literal["device", "bpm_process", "ai_skill", "script"]


class AssetEncoder(json.JSONEncoder):
    """支持 datetime 序列化的 JSON 编码器"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)
AssetStatus = Literal["planning", "development", "testing", "staging", "production", "deprecated", "archived"]
LifecyclePhase = Literal["discovery", "development", "testing", "staging", "production", "deprecation", "archived"]
DeviceSubtype = Literal["physical", "virtual"]


# ============================================================
# Asset 模型定义
# ============================================================

@dataclass
class AssetMetadata:
    """资产元数据"""
    owner: str = "unknown"
    team: str = "general"
    tags: list[str] = field(default_factory=list)
    description: str = ""
    version: str = "1.0.0"
    created_at: str = ""
    updated_at: str = ""
    documentation: str = ""

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
    id: str
    name: str
    type: AssetType
    status: AssetStatus = "planning"
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
class DeviceAsset(Asset):
    """
    设备资产（统一表示物理设备和虚拟设备/RPA Bot）

    physical: 传感器、执行器、手机等实体设备
    virtual:  RPA Bot、软件机器人等虚拟设备
    """
    type: AssetType = "device"
    device_subtype: DeviceSubtype = "physical"
    # 物理设备字段
    hardware_info: dict = field(default_factory=dict)
    protocol: str = "ws"
    # 虚拟设备字段
    bot_type: str = ""
    entry_point: str = ""
    capabilities: list[str] = field(default_factory=list)
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["device_subtype"] = self.device_subtype
        base["hardware_info"] = self.hardware_info
        base["protocol"] = self.protocol
        base["bot_type"] = self.bot_type
        base["entry_point"] = self.entry_point
        base["capabilities"] = self.capabilities
        base["input_schema"] = self.input_schema
        base["output_schema"] = self.output_schema
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
    """脚本资产"""
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
# AssetRegistry
# ============================================================

class AssetRegistry:
    """
    自动化资产注册中心

    持久化到 server/coe/asset_registry.json
    """

    ASSET_FILE = Path(__file__).parent / "asset_registry.json"

    def __init__(self, load: bool = True):
        self._assets: dict[str, Asset] = {}
        if load:
            self._load()
        logger.info("asset_registry_initialized", assets_count=len(self._assets))

    # ---- 持久化 ----

    def _load(self):
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
        try:
            self.ASSET_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": "1.0",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "assets": [asset.to_dict() for asset in self._assets.values()]
            }
            with open(self.ASSET_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("asset_registry_save_failed", error=str(e))

    def _dict_to_asset(self, d: dict) -> Optional[Asset]:
        asset_type = d.get("type")
        runtime_state = d.pop("runtime_state", None)
        try:
            if "metadata" in d and isinstance(d["metadata"], dict):
                d["metadata"] = AssetMetadata(**d["metadata"])

            if asset_type == "device":
                asset = DeviceAsset(**d)
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
        asset_id: Optional[str] = None,
        metadata: Optional[AssetMetadata] = None,
        **kwargs
    ) -> Asset:
        """注册一个新资产，返回创建的 Asset 对象

        Args:
            name: 资产名称
            asset_type: 资产类型（device / bpm_process / ai_skill / script）
            asset_id: 资产 ID（由业务方提供，确保全局唯一）。若不提供则自动生成。
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

        if not asset_id:
            asset_id = f"{asset_type}-{uuid.uuid4().hex[:8]}"

        # 按 type 创建对应的子类实例
        if asset_type == "device":
            asset = DeviceAsset(id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs)
        elif asset_type == "bpm_process":
            asset = BpmProcessAsset(id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs)
        elif asset_type == "ai_skill":
            asset = AiSkillAsset(id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs)
        elif asset_type == "script":
            asset = ScriptAsset(id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs)
        else:
            asset = Asset(id=asset_id, name=name, type=asset_type, metadata=metadata, **kwargs)

        self._assets[asset.id] = asset
        self._save()
        logger.info("asset_registered", asset_id=asset.id, name=name, type=asset_type)
        return asset

    def unregister_asset(self, asset_id: str) -> bool:
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
        return self._assets.get(asset_id)

    def list_assets(
        self,
        asset_type: Optional[AssetType] = None,
        status: Optional[AssetStatus] = None,
        owner: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> list[Asset]:
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
        return self.list_assets(asset_type=asset_type)

    def list_devices(self, device_subtype: Optional[DeviceSubtype] = None) -> list[DeviceAsset]:
        """列出设备资产，可按子类型过滤"""
        devices = [a for a in self._assets.values() if isinstance(a, DeviceAsset)]
        if device_subtype:
            devices = [d for d in devices if d.device_subtype == device_subtype]
        return devices

    # ---- 更新 ----

    def update_status(self, asset_id: str, status: AssetStatus) -> bool:
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
        asset = self._assets.get(asset_id)
        if not asset:
            return False
        asset.runtime_state.update(state)
        return True

    def advance_lifecycle(self, asset_id: str, target_phase: LifecyclePhase) -> bool:
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
        total = len(self._assets)
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for asset in self._assets.values():
            by_type[asset.type] = by_type.get(asset.type, 0) + 1
            by_status[asset.status] = by_status.get(asset.status, 0) + 1
        return {"total": total, "by_type": by_type, "by_status": by_status}

    # ---- 与 device_manager 同步 ----

    def sync_from_device_manager(self, merged_by_id: dict) -> dict:
        """
        从 device_manager.merged_by_id 自动同步设备资产。

        - 设备已在资产库：只更新 runtime_state（状态刷新）
        - 设备未在资产库：自动注册为 device 资产
        - 资产库有但 device_manager 没有：从资产库移除（离线设备）

        Args:
            merged_by_id: device_manager.merged_by_id（dev_id → state_dict）
        Returns:
            {"added": int, "updated": int, "removed": int}
        """
        added = updated = removed = 0
        now = datetime.now(timezone.utc).isoformat()

        # 同步已有设备 + 新设备
        for dev_id, state in merged_by_id.items():
            if dev_id in self._assets:
                self._assets[dev_id].runtime_state = state
                updated += 1
            else:
                device_type = state.get("type", "generic")
                device_subtype: DeviceSubtype = (
                    "virtual" if device_type in ("virtual", "rpa", "bot", "software") else "physical"
                )
                self.register_asset(
                    asset_id=dev_id,
                    name=f"{dev_id} ({device_type})",
                    asset_type="device",
                    device_subtype=device_subtype,
                    metadata=AssetMetadata(
                        owner=state.get("owner", "system"),
                        description=f"Auto-synced from device_manager: {dev_id}",
                        tags=["auto-synced", device_type],
                    ),
                )
                added += 1

        # 移除已离线的设备（device_manager 没有但资产库有的）
        for dev_id in list(self._assets.keys()):
            asset = self._assets[dev_id]
            if asset.type == "device" and dev_id not in merged_by_id:
                self._assets[dev_id].status = "deprecated"
                self._assets[dev_id].metadata.updated_at = now
                removed += 1

        self._save()
        logger.info("device_sync_completed", added=added, updated=updated, removed=removed)
        return {"added": added, "updated": updated, "removed": removed}


# ============================================================
# 全局单例
# ============================================================
_global_registry: Optional[AssetRegistry] = None


def get_registry() -> AssetRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = AssetRegistry()
    return _global_registry
