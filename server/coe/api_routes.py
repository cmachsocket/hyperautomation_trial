"""
自动化资产库 REST API 路由

挂载到: /api/coe/assets/*
"""

from __future__ import annotations

import json
from aiohttp import web
from aiohttp.web import Request, Response
from .asset_registry import (
    AssetRegistry, AssetType, AssetStatus, AssetMetadata,
    get_registry, AssetEncoder
)
import structlog

logger = structlog.get_logger(__name__)

routes = web.RouteTableDef()


def json_response(data: dict, status: int = 200) -> Response:
    return Response(
        text=json.dumps(data, ensure_ascii=False, cls=AssetEncoder),
        content_type="application/json",
        status=status
    )


def error_response(message: str, status: int = 400) -> Response:
    return json_response({"error": message}, status)


# ---- 列表 & 摘要 ----

@routes.get("/api/coe/assets")
async def list_assets(request: Request) -> Response:
    """GET /api/coe/assets?type=&status=&owner=&tag=&summary=1"""
    registry: AssetRegistry = request.app["asset_registry"]
    summary_only = request.query.get("summary") == "1"

    if summary_only:
        return json_response(registry.summary())

    filters = {
        "asset_type": request.query.get("type"),
        "status": request.query.get("status"),
        "owner": request.query.get("owner"),
        "tag": request.query.get("tag"),
    }
    filters = {k: v for k, v in filters.items() if v}
    assets = registry.list_assets(**filters) if filters else list(registry._assets.values())
    return json_response({
        "total": len(assets),
        "assets": [a.to_dict() for a in assets]
    })


@routes.get("/api/coe/assets/summary")
async def asset_summary(request: Request) -> Response:
    """GET /api/coe/assets/summary — 统计摘要"""
    registry: AssetRegistry = request.app["asset_registry"]
    return json_response(registry.summary())


# ---- 单个资产 ----

@routes.get("/api/coe/assets/{asset_id}")
async def get_asset(request: Request) -> Response:
    """GET /api/coe/assets/{asset_id}"""
    registry: AssetRegistry = request.app["asset_registry"]
    asset = registry.get_asset(request.match_info["asset_id"])
    if not asset:
        return error_response("Asset not found", 404)
    return json_response(asset.to_dict())


@routes.post("/api/coe/assets")
async def register_asset(request: Request) -> Response:
    """POST /api/coe/assets — 注册新资产

    Body:
    {
        "name": "My RPA Bot",
        "type": "virtual_device",
        "metadata": {
            "owner": "alice",
            "team": "iot",
            "tags": ["rpa", "web"],
            "description": "Web scraper bot"
        },
        // type-specific fields...
    }
    """
    registry: AssetRegistry = request.app["asset_registry"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON body")

    name = body.get("name")
    asset_type = body.get("type")
    metadata_dict = body.get("metadata", {})

    if not name or not asset_type:
        return error_response("name and type are required")
    if asset_type not in ["device", "bpm_process", "ai_skill", "script"]:
        return error_response(f"Invalid type: {asset_type}")

    metadata = AssetMetadata(**metadata_dict) if metadata_dict else None
    asset_id = body.get("id")  # 可选，业务方提供
    extra = {k: v for k, v in body.items() if k not in ("name", "type", "metadata", "id")}

    asset = registry.register_asset(name=name, asset_type=asset_type, asset_id=asset_id, metadata=metadata, **extra)
    logger.info("asset_registered_via_api", asset_id=asset.id, name=name)
    return json_response(asset.to_dict(), status=201)


@routes.patch("/api/coe/assets/{asset_id}")
async def update_asset(request: Request) -> Response:
    """PATCH /api/coe/assets/{asset_id} — 部分更新"""
    registry: AssetRegistry = request.app["asset_registry"]
    asset_id = request.match_info["asset_id"]
    asset = registry.get_asset(asset_id)
    if not asset:
        return error_response("Asset not found", 404)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON body")

    if "name" in body:
        asset.name = body["name"]
    if "status" in body:
        registry.update_status(asset_id, body["status"])
    if "metadata" in body:
        for k, v in body["metadata"].items():
            if hasattr(asset.metadata, k):
                setattr(asset.metadata, k, v)
    if "runtime_state" in body:
        asset.runtime_state.update(body["runtime_state"])

    registry._save()
    return json_response(asset.to_dict())


@routes.delete("/api/coe/assets/{asset_id}")
async def delete_asset(request: Request) -> Response:
    """DELETE /api/coe/assets/{asset_id} — 软删除（归档）"""
    registry: AssetRegistry = request.app["asset_registry"]
    asset_id = request.match_info["asset_id"]
    success = registry.unregister_asset(asset_id)
    if not success:
        return error_response("Asset not found", 404)
    return json_response({"status": "archived", "asset_id": asset_id})


# ---- 生命周期 ----

@routes.post("/api/coe/assets/{asset_id}/lifecycle")
async def advance_lifecycle(request: Request) -> Response:
    """POST /api/coe/assets/{asset_id}/lifecycle — 推进生命周期阶段

    Body: {"phase": "testing"}
    // discovery | development | testing | staging | production | deprecation | archived
    """
    registry: AssetRegistry = request.app["asset_registry"]
    asset_id = request.match_info["asset_id"]
    asset = registry.get_asset(asset_id)
    if not asset:
        return error_response("Asset not found", 404)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON body")

    phase = body.get("phase")
    if not phase:
        return error_response("phase is required")

    success = registry.advance_lifecycle(asset_id, phase)
    if not success:
        return error_response(f"Invalid phase: {phase}")
    asset = registry.get_asset(asset_id)
    return json_response(asset.to_dict())


# ---- 运行时状态 ----

@routes.put("/api/coe/assets/{asset_id}/runtime")
async def update_runtime_state(request: Request) -> Response:
    """PUT /api/coe/assets/{asset_id}/runtime — 更新运行时状态"""
    registry: AssetRegistry = request.app["asset_registry"]
    asset_id = request.match_info["asset_id"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON body")

    success = registry.update_runtime_state(asset_id, body)
    if not success:
        return error_response("Asset not found", 404)
    return json_response({"status": "ok", "asset_id": asset_id})


# ---- 同步物理设备 ----

@routes.post("/api/coe/assets/sync/devices")
async def sync_devices(request: Request) -> Response:
    """POST /api/coe/assets/sync/devices — 从 device_manager 同步物理设备"""
    registry: AssetRegistry = request.app["asset_registry"]
    device_manager = request.app.get("device_manager")
    if not device_manager:
        return error_response("device_manager not available", 500)

    result = registry.sync_from_device_manager(device_manager.merged_by_id)
    return json_response({**result, "total": len(registry._assets)})


# ---- 辅助函数 ----

def setup_asset_routes(app: web.Application):
    """将资产库路由注册到 aiohttp app"""
    registry = get_registry()
    app["asset_registry"] = registry
    app.router.add_routes(routes)
    logger.info("asset_registry_routes_mounted")
