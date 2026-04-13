"""
自动化资产库 REST API 路由

挂载到: /api/coe/assets/*
"""

from __future__ import annotations

import json

import structlog
from quart import Blueprint, Response, current_app, request

from .asset_registry import AssetEncoder, AssetRegistry, AssetStatus, AssetType, LifecyclePhase, get_registry

logger = structlog.get_logger(__name__)

bp = Blueprint("coe_assets", __name__)

VALID_ASSET_TYPES: tuple[AssetType, ...] = ("device", "workflow", "ai_skill", "script")
VALID_ASSET_STATUS: tuple[AssetStatus, ...] = (
    "planning",
    "development",
    "testing",
    "staging",
    "production",
    "deprecated",
    "archived",
)
VALID_LIFECYCLE_PHASES: tuple[LifecyclePhase, ...] = (
    "discovery",
    "development",
    "testing",
    "staging",
    "production",
    "deprecation",
    "archived",
)


def json_response(data: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(data, ensure_ascii=False, cls=AssetEncoder),
        status=status,
        content_type="application/json",
    )


def error_response(message: str, status: int = 400) -> Response:
    return json_response({"error": message}, status)


def _registry() -> AssetRegistry:
    return current_app.config["asset_registry"]


@bp.route("/api/coe/assets", methods=["GET"])
async def list_assets() -> Response:
    """GET /api/coe/assets?type=&status=&owner=&tag=&summary=1"""
    registry = _registry()
    summary_only = request.args.get("summary") == "1"

    if summary_only:
        return json_response(registry.summary())

    asset_type_query = request.args.get("type") or None
    status_query = request.args.get("status") or None
    owner = request.args.get("owner") or None
    tag = request.args.get("tag") or None

    if asset_type_query and asset_type_query not in VALID_ASSET_TYPES:
        return error_response(f"Invalid type: {asset_type_query}")
    if status_query and status_query not in VALID_ASSET_STATUS:
        return error_response(f"Invalid status: {status_query}")

    asset_type: AssetType | None = asset_type_query if asset_type_query in VALID_ASSET_TYPES else None
    status: AssetStatus | None = status_query if status_query in VALID_ASSET_STATUS else None

    has_filters = bool(asset_type or status or owner or tag)
    assets = (
        registry.list_assets(
            asset_type=asset_type,
            status=status,
            owner=owner,
            tag=tag,
        )
        if has_filters
        else list(registry._assets.values())
    )
    return json_response({"total": len(assets), "assets": [a.to_dict() for a in assets]})


@bp.route("/api/coe/assets/summary", methods=["GET"])
async def asset_summary() -> Response:
    """GET /api/coe/assets/summary — 统计摘要"""
    return json_response(_registry().summary())


@bp.route("/api/coe/assets/<asset_id>", methods=["GET"])
async def get_asset(asset_id: str) -> Response:
    """GET /api/coe/assets/{asset_id}"""
    asset = _registry().get_asset(asset_id)
    if not asset:
        return error_response("Asset not found", 404)
    return json_response(asset.to_dict())


@bp.route("/api/coe/assets/<asset_id>", methods=["PATCH"])
async def update_asset(asset_id: str) -> Response:
    """PATCH /api/coe/assets/{asset_id} — 部分更新"""
    registry = _registry()
    asset = registry.get_asset(asset_id)
    if not asset:
        return error_response("Asset not found", 404)

    try:
        body = await request.get_json()
    except Exception:
        return error_response("Invalid JSON body")

    if not isinstance(body, dict):
        return error_response("JSON body must be an object")

    if "name" in body:
        asset.name = body["name"]
    if "status" in body:
        new_status = body["status"]
        if new_status not in VALID_ASSET_STATUS:
            return error_response(f"Invalid status: {new_status}")
        registry.update_status(asset_id, new_status)
    if "metadata" in body and isinstance(body["metadata"], dict):
        for key, value in body["metadata"].items():
            if hasattr(asset.metadata, key):
                setattr(asset.metadata, key, value)
    if "runtime_state" in body and isinstance(body["runtime_state"], dict):
        asset.runtime_state.update(body["runtime_state"])

    registry._save()
    return json_response(asset.to_dict())


@bp.route("/api/coe/assets/<asset_id>", methods=["DELETE"])
async def delete_asset(asset_id: str) -> Response:
    """DELETE /api/coe/assets/{asset_id} — 软删除（归档）"""
    success = _registry().unregister_asset(asset_id)
    if not success:
        return error_response("Asset not found", 404)
    return json_response({"status": "archived", "asset_id": asset_id})


@bp.route("/api/coe/assets/<asset_id>/lifecycle", methods=["GET"])
async def get_lifecycle(asset_id: str) -> Response:
    """GET /api/coe/assets/{asset_id}/lifecycle — 获取生命周期状态和可推进阶段"""
    lifecycle = _registry().get_lifecycle_info(asset_id)
    if not lifecycle:
        return error_response("Asset not found", 404)
    return json_response(lifecycle)


@bp.route("/api/coe/assets/<asset_id>/lifecycle", methods=["POST"])
async def advance_lifecycle(asset_id: str) -> Response:
    """POST /api/coe/assets/{asset_id}/lifecycle — 推进生命周期阶段

    Body: {"phase": "testing"} 或 {"action": "next"}
    // discovery | development | testing | staging | production | deprecation | archived
    """
    registry = _registry()
    asset = registry.get_asset(asset_id)
    if not asset:
        return error_response("Asset not found", 404)

    try:
        body = await request.get_json()
    except Exception:
        return error_response("Invalid JSON body")

    if not isinstance(body, dict):
        return error_response("JSON body must be an object")

    if body.get("action") == "next":
        next_phase = registry.advance_lifecycle_next(asset_id)
        if not next_phase:
            return error_response("No next phase available")
        updated_lifecycle = registry.get_lifecycle_info(asset_id)
        asset = registry.get_asset(asset_id)
        if not asset or not updated_lifecycle:
            return error_response("Asset not found", 404)
        return json_response(
            {
                "asset": asset.to_dict(),
                "lifecycle": updated_lifecycle,
                "advanced_to": next_phase,
            }
        )

    phase = body.get("phase")
    if not phase:
        return error_response("phase is required")
    if phase not in VALID_LIFECYCLE_PHASES:
        return error_response(f"Invalid phase: {phase}")

    success = registry.advance_lifecycle(asset_id, phase)
    if not success:
        return error_response(f"Invalid phase: {phase}")
    asset = registry.get_asset(asset_id)
    if not asset:
        return error_response("Asset not found", 404)
    lifecycle = registry.get_lifecycle_info(asset_id)
    return json_response(
        {
            "asset": asset.to_dict(),
            "lifecycle": lifecycle,
            "advanced_to": phase,
        }
    )


@bp.route("/api/coe/assets/<asset_id>/runtime", methods=["PUT"])
async def update_runtime_state(asset_id: str) -> Response:
    """PUT /api/coe/assets/{asset_id}/runtime — 更新运行时状态"""
    try:
        body = await request.get_json()
    except Exception:
        return error_response("Invalid JSON body")

    success = _registry().update_runtime_state(asset_id, body)
    if not success:
        return error_response("Asset not found", 404)
    return json_response({"status": "ok", "asset_id": asset_id})


@bp.route("/api/coe/assets/sync/devices", methods=["POST"])
async def sync_devices() -> Response:
    """POST /api/coe/assets/sync/devices — 从 device_manager 同步物理设备"""
    registry = _registry()
    device_manager = current_app.config.get("device_manager")
    if not device_manager:
        return error_response("device_manager not available", 500)

    result = registry.sync_from_device_manager(device_manager.merged_by_id)
    return json_response({**result, "total": len(registry._assets)})


@bp.route("/api/coe/assets/sync/scripts", methods=["POST"])
async def sync_scripts() -> Response:
    """POST /api/coe/assets/sync/scripts — 从 src/scripts 同步脚本资产

    Body (optional):
    {
      "id_strategy": "name_md5",  // name_md5 | name_mtime | path
            "archive_missing": true,
            "recursive": true,
            "extensions": [".js", ".ts", ".py", ".sh"]
    }
    """
    registry = _registry()
    try:
        body = await request.get_json(silent=True) or {}
    except Exception:
        return error_response("Invalid JSON body")

    if not isinstance(body, dict):
        return error_response("JSON body must be an object")

    id_strategy = body.get("id_strategy", "name_md5")
    archive_missing = body.get("archive_missing", True)
    recursive = body.get("recursive", True)
    extensions = body.get("extensions")

    if id_strategy not in ("name_md5", "name_mtime", "path"):
        return error_response(f"Invalid id_strategy: {id_strategy}")
    if not isinstance(archive_missing, bool):
        return error_response("archive_missing must be boolean")
    if not isinstance(recursive, bool):
        return error_response("recursive must be boolean")
    if extensions is not None:
        if not isinstance(extensions, list) or not all(isinstance(ext, str) and ext.strip() for ext in extensions):
            return error_response("extensions must be a string array")
        extensions_tuple: tuple[str, ...] | None = tuple(extensions)
    else:
        extensions_tuple = None

    result = registry.sync_from_scripts_dir(
        id_strategy=id_strategy,
        archive_missing=archive_missing,
        recursive=recursive,
        extensions=extensions_tuple,
    )
    return json_response({**result, "total": len(registry._assets)})


def setup_asset_routes(app):
    """将资产库路由注册到 Quart app"""
    registry = get_registry()
    app.config["asset_registry"] = registry
    app.register_blueprint(bp)
    logger.info("asset_registry_routes_mounted")