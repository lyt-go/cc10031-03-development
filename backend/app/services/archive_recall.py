"""归档回查领域服务：按时间分区维护序列窗口的可回查范围、保存位置与冷档校验进度。

场景基线：时间分区 2026-09-01 含三个归并窗口——首窗已完成冷档校验，
热区接近轮换上限；第二窗原确认值 20 仍可在热区读取，更正值 21 登记为
待核修订；第三窗无修订。更正验收、范围查询、清单生成与重算都围绕这份
基线推进：未迁冷的数据在任何查询结果里都不得标记为已归档，待核的新
修订走单独入口，归档中断后按上次已确认的窗口继续重算。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CHECKPOINT_NAME = "archive_recall_progress"
DEFAULT_PARTITION = "2026-09-01"


def _seq(row: dict[str, Any]) -> int:
    try:
        return int(row.get("窗口序号", 0))
    except (TypeError, ValueError):
        return 0


class ArchiveRecallService(BaseModuleService):
    MODULE = "archive_recall"
    ENTITY = "回查窗口"
    OPERATION_TERM = "核验"
    LIST_FIELDS = ["窗口编号", "时间分区", "窗口序号", "确认值", "有效值", "保存位置", "冷档校验", "待核修订", "回查状态"]
    REQUIRED_FIELDS = ["窗口编号", "时间分区", "窗口序号"]
    STATUS_ORDER = ["待核验", "校验中", "已归档", "校验失败"]
    ACTION_RULES = {"提交核验": "校验中", "确认归档": "已归档", "标记失败": "校验失败"}
    NEGATIVE_ACTIONS = ["标记失败"]

    def __init__(self) -> None:
        # 热区水位：接近轮换上限，是冷档校验与迁移推进的背景压力
        self._hot_zone = {"用量": 96, "上限": 100}
        # 副本台账：每个窗口在热区/冷区的保存副本及其核验状态。
        # 第二窗的冷副本仍携带原确认值 20——更正值 21 尚未迁冷。
        self._copies: list[dict[str, Any]] = [
            {"copy_id": "CP-20260901-01-HOT", "window_id": "WIN-20260901-01", "location": "热区", "state": "可读", "value": 10},
            {"copy_id": "CP-20260901-01-COLD", "window_id": "WIN-20260901-01", "location": "冷区", "state": "已校验", "value": 10},
            {"copy_id": "CP-20260901-02-HOT", "window_id": "WIN-20260901-02", "location": "热区", "state": "可读", "value": 20},
            {"copy_id": "CP-20260901-02-COLD", "window_id": "WIN-20260901-02", "location": "冷区", "state": "待核验", "value": 20},
            {"copy_id": "CP-20260901-03-HOT", "window_id": "WIN-20260901-03", "location": "热区", "state": "可读", "value": 30},
            {"copy_id": "CP-20260901-03-COLD", "window_id": "WIN-20260901-03", "location": "冷区", "state": "待核验", "value": 30},
        ]
        # 更正记录（新修订）：单独入口登记，验收前不得影响有效值，
        # 未迁冷前不得让窗口被当成已归档。
        self._revisions: dict[str, dict[str, Any]] = {
            "REV-20260901-02-21": {
                "revision_id": "REV-20260901-02-21",
                "window_id": "WIN-20260901-02",
                "value": 21,
                "state": "待核",
                "migrated_to_cold": False,
                "trace": "关联数据：ROLL-20260901-02 归并结果 / DOWN-20260901 降采样产物",
            },
        }
        self._queries: dict[str, dict[str, Any]] = {}
        self._checklists: dict[str, dict[str, Any]] = {}
        self._verify_records: list[dict[str, Any]] = []

    # -- 场景基线查询 --

    def _window_rows(self, partition: str) -> list[dict[str, Any]]:
        rows = [r for r in store.rows(self.MODULE) if r.get("时间分区") == partition]
        return sorted(rows, key=_seq)

    def _copies_of(self, window_id: str) -> list[dict[str, Any]]:
        return [c for c in self._copies if c["window_id"] == window_id]

    def _cold_verified(self, window_id: str) -> bool:
        return any(c["location"] == "冷区" and c["state"] == "已校验" for c in self._copies_of(window_id))

    def _accepted_revision(self, window_id: str) -> dict[str, Any] | None:
        for rev in self._revisions.values():
            if rev["window_id"] == window_id and rev["state"] == "已验收":
                return rev
        return None

    def _pending_revision(self, window_id: str) -> dict[str, Any] | None:
        for rev in self._revisions.values():
            if rev["window_id"] == window_id and rev["state"] == "待核":
                return rev
        return None

    def _hot_zone_status(self) -> dict[str, Any]:
        return {
            **self._hot_zone,
            "接近轮换上限": self._hot_zone["用量"] >= self._hot_zone["上限"] - 5,
        }

    # -- 可回查范围视图 --

    def recallable_coverage(self, partition: str) -> dict[str, Any]:
        """按时间分区计算实际可回查范围：每个窗口的有效值、保存位置与归档真实性。"""
        windows = []
        for row in self._window_rows(partition):
            wid = row["窗口编号"]
            accepted = self._accepted_revision(wid)
            cold_verified = self._cold_verified(wid)
            confirmed = row.get("确认值")
            if accepted is not None and not accepted.get("migrated_to_cold"):
                # 更正已验收但尚未迁冷：有效值取更正值，仍在热区回查，不得标记已归档
                effective = accepted["value"]
                location = "热区"
                archived = False
            elif cold_verified:
                effective = confirmed
                location = "冷区"
                archived = True
            else:
                effective = confirmed
                location = "热区"
                archived = False
            # 每个取值各自能追到关联数据：确认值追到保存副本，更正值追到修订记录
            traces = {str(confirmed): [c["copy_id"] for c in self._copies_of(wid)]}
            if accepted is not None:
                traces[str(accepted["value"])] = [accepted["revision_id"]]
            windows.append({
                "窗口编号": wid,
                "窗口序号": row.get("窗口序号"),
                "确认值": confirmed,
                "有效值": effective,
                "保存位置": location,
                "冷档校验": "已通过" if cold_verified else "待核验",
                "已归档": archived,
                "回查状态": "可回查",
                "待核修订": (self._pending_revision(wid) or {}).get("revision_id", "无"),
                "已验收修订": (accepted or {}).get("revision_id", "无"),
                "关联追踪": traces,
            })
        return {
            "时间分区": partition,
            "热区水位": self._hot_zone_status(),
            "windows": windows,
            # 待核的新修订：单独入口，不能被当成已归档
            "pending_revisions": [dict(r) for r in self._revisions.values() if r["state"] == "待核"],
        }

    def archive_dashboard(self) -> dict[str, Any]:
        """归档看板：列出每个窗口在哪个保存位置可回查，并呈现尚待核验的副本。"""
        partitions = sorted({r.get("时间分区") for r in store.rows(self.MODULE) if r.get("时间分区")})
        windows = []
        for part in partitions:
            coverage = self.recallable_coverage(part)
            for win in coverage["windows"]:
                windows.append({**win, "副本": [dict(c) for c in self._copies_of(win["窗口编号"])]})
        return {
            "windows": windows,
            # 尚待核验的副本
            "pending_copies": [dict(c) for c in self._copies if c["state"] in ("待核验", "校验失败")],
            # 待核的新修订：单独入口
            "pending_revisions": [dict(r) for r in self._revisions.values() if r["state"] == "待核"],
            "热区水位": self._hot_zone_status(),
            "verify_records": list(self._verify_records),
        }

    # -- 两段式范围查询：先准备入口，读取时才定值 --

    def prepare_range_query(self, partition: str) -> dict[str, Any]:
        """准备首次可回查查询入口：入口已准备但尚未发给读者。"""
        query_id = f"QRY-{partition}-{len(self._queries) + 1}"
        entry = {
            "query_id": query_id,
            "partition": partition,
            "state": "已准备",
            "delivered": False,
            "prepared_at": time.time(),
        }
        self._queries[query_id] = entry
        return {**entry, "message": "查询入口已准备，尚未发给读者"}

    def read_range_query(self, query_id: str) -> dict[str, Any]:
        """完成范围查询：以读取时刻的覆盖结果为准，并返回相同覆盖的序列索引。"""
        entry = self._queries.get(query_id)
        if entry is None:
            return {"error": f"查询入口 {query_id} 不存在"}
        coverage = self.recallable_coverage(entry["partition"])
        index_svc = self._safe_import("app.services.series_index", "SeriesIndexService")()
        located = index_svc.locate_by_coverage(entry["partition"], coverage=coverage)
        entry["state"] = "已送达"
        entry["delivered"] = True
        entry["read_at"] = time.time()
        return {
            "query_id": query_id,
            "state": entry["state"],
            "coverage": coverage,
            "index": located["entries"],
            "message": "范围查询完成，结果已送达读者",
        }

    # -- 更正记录：验收后生效，未迁冷不算已归档 --

    def accept_revision(self, revision_id: str) -> dict[str, Any]:
        """正式验收更正记录：验收后有效值切换为更正值，但未迁冷前不算已归档。"""
        rev = self._revisions.get(revision_id)
        if rev is None:
            return {"error": f"更正记录 {revision_id} 不存在"}
        if rev["state"] == "已验收":
            return {"revision": dict(rev), "message": "更正记录已验收，无需重复操作"}
        rev["state"] = "已验收"
        rev["accepted_at"] = time.time()
        self._sync_window_row(rev["window_id"])
        return {"revision": dict(rev), "message": "更正记录已正式验收"}

    # -- 校验清单与逐项核验 --

    def generate_checklist(self, partition: str) -> dict[str, Any]:
        """从未完成范围生成下一轮校验清单：已通过冷档校验的窗口不再排入。"""
        items: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for row in self._window_rows(partition):
            wid = row["窗口编号"]
            if self._cold_verified(wid):
                excluded.append({"窗口编号": wid, "原因": "已通过冷档校验"})
                continue
            for copy in self._copies_of(wid):
                if copy["state"] in ("待核验", "校验失败"):
                    items.append({
                        "窗口编号": wid,
                        "copy_id": copy["copy_id"],
                        "location": copy["location"],
                        "value": copy["value"],
                    })
        round_no = sum(1 for c in self._checklists.values() if c["partition"] == partition) + 1
        checklist = {
            "checklist_id": f"CHK-{partition}-{round_no}",
            "partition": partition,
            "round": round_no,
            "items": items,
            "excluded": excluded,
            "created_at": time.time(),
        }
        self._checklists[checklist["checklist_id"]] = checklist
        return checklist

    def verify_copy(self, window_id: str, copy_id: str, ok: bool, note: str = "") -> dict[str, Any]:
        """逐项核验副本：校验失败要注明对应窗口及失败的副本。"""
        copy = next((c for c in self._copies if c["copy_id"] == copy_id and c["window_id"] == window_id), None)
        if copy is None:
            return {"error": f"副本 {copy_id} 在窗口 {window_id} 下不存在"}
        copy["state"] = "已校验" if ok else "校验失败"
        record: dict[str, Any] = {
            "窗口编号": window_id,
            "copy_id": copy_id,
            "ok": ok,
            "note": note,
            "at": time.time(),
        }
        if not ok:
            # 注明对应窗口及失败的副本
            record["失败副本"] = copy_id
        self._verify_records.append(record)
        if ok and copy["location"] == "冷区":
            # 冷档校验推进归档水位：上次已确认窗口随之前移
            self._write_checkpoint(CHECKPOINT_NAME, {
                "last_confirmed_window": window_id,
                "interrupted": False,
            })
        self._sync_window_row(window_id)
        return {"record": record, "cold_verified": self._cold_verified(window_id)}

    # -- 归档中断与重算续算 --

    def interrupt_archive(self, partition: str = DEFAULT_PARTITION) -> dict[str, Any]:
        """标记归档中断：保留上次已确认窗口，供查询决定是否按它继续重算。"""
        checkpoint = self._read_checkpoint(CHECKPOINT_NAME) or {}
        if not checkpoint.get("last_confirmed_window"):
            verified = [r["窗口编号"] for r in self._window_rows(partition) if self._cold_verified(r["窗口编号"])]
            checkpoint["last_confirmed_window"] = verified[-1] if verified else None
        checkpoint["interrupted"] = True
        self._write_checkpoint(CHECKPOINT_NAME, checkpoint)
        return {"message": "归档已中断", "checkpoint": checkpoint}

    def recompute(
        self,
        window_id: str | None = None,
        index_handle: str | None = None,
        resume: bool = False,
        partition: str = DEFAULT_PARTITION,
    ) -> dict[str, Any]:
        """从范围查询返回的索引定位窗口并开始重算；归档中断时可按上次已确认窗口续算。"""
        checkpoint = self._read_checkpoint(CHECKPOINT_NAME)
        resumed_from = None
        interrupted = bool(checkpoint and checkpoint.get("interrupted"))
        if resume:
            last_confirmed = (checkpoint or {}).get("last_confirmed_window")
            if not last_confirmed:
                return {"error": "没有可续算的检查点：上次已确认窗口未知"}
            resumed_from = last_confirmed
            window_id = self._next_window(partition, last_confirmed)
            if window_id is None:
                return {"error": f"分区 {partition} 中 {last_confirmed} 之后没有待重算窗口"}
        elif index_handle:
            window_id = self._window_from_index(partition, index_handle)
            if window_id is None:
                return {"error": f"索引句柄 {index_handle} 未定位到任何窗口"}
        if not window_id:
            return {"error": "缺少重算目标：请提供窗口编号、索引句柄或选择续算"}
        row = next((r for r in store.rows(self.MODULE) if r.get("窗口编号") == window_id), None)
        if row is None:
            return {"error": f"窗口 {window_id} 不存在"}
        coverage = self.recallable_coverage(row["时间分区"])
        win = next(w for w in coverage["windows"] if w["窗口编号"] == window_id)
        # 重算采用值：已验收的更正值优先，否则沿用原确认值
        adopted = win["有效值"]
        pending = [w["窗口编号"] for w in coverage["windows"] if w["冷档校验"] != "已通过"]
        self._write_checkpoint(CHECKPOINT_NAME, {
            "last_confirmed_window": window_id,
            "interrupted": False,
            "adopted_value": adopted,
            "location": win["保存位置"],
        })
        return {
            "窗口编号": window_id,
            "adopted_value": adopted,
            "location": win["保存位置"],
            "archived": win["已归档"],
            "resumed_from": resumed_from,
            "archive_interrupted": interrupted,
            "pending_windows": pending,
            "关联追踪": win["关联追踪"],
            "message": f"重算完成：采用值 {adopted}（{win['保存位置']}）",
        }

    def _next_window(self, partition: str, after_window_id: str) -> str | None:
        rows = self._window_rows(partition)
        after_seq = next((_seq(r) for r in rows if r.get("窗口编号") == after_window_id), 0)
        for row in rows:
            if _seq(row) > after_seq:
                return row["窗口编号"]
        return None

    def _window_from_index(self, partition: str, handle: str) -> str | None:
        index_svc = self._safe_import("app.services.series_index", "SeriesIndexService")()
        located = index_svc.locate_by_coverage(partition, coverage=self.recallable_coverage(partition))
        for entry in located["entries"]:
            if entry["索引编号"] == handle:
                return entry["窗口编号"]
        return None

    # -- 展示字段同步：让通用列表反映推导后的真实状态 --

    def _sync_window_row(self, window_id: str) -> None:
        row = next((r for r in store.rows(self.MODULE) if r.get("窗口编号") == window_id), None)
        if row is None:
            return
        coverage = self.recallable_coverage(row["时间分区"])
        win = next((w for w in coverage["windows"] if w["窗口编号"] == window_id), None)
        if win is None:
            return
        row["有效值"] = win["有效值"]
        row["保存位置"] = win["保存位置"]
        row["冷档校验"] = win["冷档校验"]
        row["待核修订"] = win["待核修订"]
        row["回查状态"] = win["回查状态"]
        failed = any(c["state"] == "校验失败" for c in self._copies_of(window_id))
        row["status"] = "校验失败" if failed else ("已归档" if win["已归档"] else "待核验")
        row["pending"] = not win["已归档"]
        row["abnormal"] = failed
