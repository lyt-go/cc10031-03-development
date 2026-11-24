"""归档回查接口：可回查范围视图、归档看板、两段式范围查询、校验清单与重算续算。

这些路径必须抢在通用资源路由 /api/{module} 之前注册，
否则 /api/archive_recall/coverage 这类固定路径会被动态段抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import service_for

router = APIRouter(prefix="/api", tags=["归档回查"])


class PrepareQueryPayload(BaseModel):
    partition: str


class ChecklistPayload(BaseModel):
    partition: str


class VerifyPayload(BaseModel):
    window_id: str
    copy_id: str
    ok: bool
    note: str = ""


class RecomputePayload(BaseModel):
    partition: str = "2026-09-01"
    window_id: str | None = None
    index_handle: str | None = None
    resume: bool = False


def _recall_service():
    service = service_for("archive_recall")
    if service is None:
        raise HTTPException(status_code=404, detail="归档回查模块未登记")
    return service


def _unwrap(result: dict[str, Any]) -> dict[str, Any]:
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/archive_recall/coverage")
def recallable_coverage(partition: str = Query(...)) -> dict[str, Any]:
    """按时间分区展示序列可回查范围：实际可回查窗口与保存位置。"""
    return _recall_service().recallable_coverage(partition)


@router.get("/archive_recall/dashboard")
def archive_dashboard() -> dict[str, Any]:
    """归档看板：每个窗口在哪个保存位置可回查，以及尚待核验的副本。"""
    return _recall_service().archive_dashboard()


@router.post("/archive_recall/queries")
def prepare_query(payload: PrepareQueryPayload) -> dict[str, Any]:
    """准备首次可回查查询入口（已准备，尚未发给读者）。"""
    return _recall_service().prepare_range_query(payload.partition)


@router.post("/archive_recall/queries/{query_id}/read")
def read_query(query_id: str) -> dict[str, Any]:
    """完成范围查询：返回覆盖结果与相同覆盖的序列索引。"""
    return _unwrap(_recall_service().read_range_query(query_id))


@router.post("/archive_recall/revisions/{revision_id}/accept")
def accept_revision(revision_id: str) -> dict[str, Any]:
    """正式验收更正记录：验收后读取采用更正值，但未迁冷不算已归档。"""
    return _unwrap(_recall_service().accept_revision(revision_id))


@router.post("/archive_recall/checklists")
def generate_checklist(payload: ChecklistPayload) -> dict[str, Any]:
    """从未完成范围生成下一轮校验清单，已通过校验的窗口不再排入。"""
    return _recall_service().generate_checklist(payload.partition)


@router.post("/archive_recall/verify")
def verify_copy(payload: VerifyPayload) -> dict[str, Any]:
    """逐项核验副本；失败记录注明对应窗口及失败的副本。"""
    return _unwrap(_recall_service().verify_copy(
        payload.window_id, payload.copy_id, payload.ok, payload.note))


@router.post("/archive_recall/recompute")
def recompute(payload: RecomputePayload) -> dict[str, Any]:
    """按索引句柄或窗口编号开始重算；归档中断时可按上次已确认窗口续算。"""
    return _unwrap(_recall_service().recompute(
        window_id=payload.window_id,
        index_handle=payload.index_handle,
        resume=payload.resume,
        partition=payload.partition,
    ))


@router.post("/archive_recall/interrupt")
def interrupt_archive() -> dict[str, Any]:
    """标记归档中断：保留上次已确认窗口供续算决策。"""
    return _recall_service().interrupt_archive()


@router.get("/series_index/locate")
def locate_by_coverage(partition: str = Query(...)) -> dict[str, Any]:
    """序列索引以相同覆盖结果定位数据。"""
    service = service_for("series_index")
    if service is None or not hasattr(service, "locate_by_coverage"):
        raise HTTPException(status_code=404, detail="序列索引模块未登记")
    return _unwrap(service.locate_by_coverage(partition))
