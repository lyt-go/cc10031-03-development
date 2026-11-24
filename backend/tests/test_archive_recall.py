"""归档回查场景测试：可回查范围视图、归档看板、校验清单、两段式查询与中断续算。

场景基线：分区 2026-09-01 三个窗口——首窗完成冷档校验，热区接近轮换上限，
第二窗原确认值 20 仍在热区可读、更正值 21 待核，第三窗无修订。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def setup_data_dir():
    """检查点写入临时目录，避免污染真实数据。"""
    with tempfile.TemporaryDirectory() as tmp:
        with patch("app.services.base.DATA_ROOT", Path(tmp)):
            yield Path(tmp)


def fresh_service():
    from app.services.archive_recall import ArchiveRecallService
    return ArchiveRecallService()


class TestRecallableCoverage:
    """按时间分区展示实际可回查窗口和保存位置。"""

    def test_coverage_shows_actual_recallable_windows_and_locations(self, setup_data_dir):
        svc = fresh_service()
        cov = svc.recallable_coverage("2026-09-01")
        wins = {w["窗口编号"]: w for w in cov["windows"]}
        # 首窗完成冷档校验 → 冷区可回查、已归档
        assert wins["WIN-20260901-01"]["保存位置"] == "冷区"
        assert wins["WIN-20260901-01"]["已归档"] is True
        # 另外两窗仍在热区回查，不得说成已归档
        assert wins["WIN-20260901-02"]["保存位置"] == "热区"
        assert wins["WIN-20260901-02"]["已归档"] is False
        assert wins["WIN-20260901-03"]["保存位置"] == "热区"
        assert wins["WIN-20260901-03"]["已归档"] is False
        # 热区接近轮换上限
        assert cov["热区水位"]["接近轮换上限"] is True
        # 待核的新修订有单独入口
        assert [r["revision_id"] for r in cov["pending_revisions"]] == ["REV-20260901-02-21"]

    def test_accepted_revision_not_migrated_is_not_archived(self, setup_data_dir):
        svc = fresh_service()
        svc.accept_revision("REV-20260901-02-21")
        cov = svc.recallable_coverage("2026-09-01")
        w2 = next(w for w in cov["windows"] if w["窗口编号"] == "WIN-20260901-02")
        # 更正值 21 已验收但尚未迁冷：有效值取 21，仍在热区，不算已归档
        assert w2["有效值"] == 21
        assert w2["保存位置"] == "热区"
        assert w2["已归档"] is False
        # 两值各自能追到关联数据
        assert "20" in w2["关联追踪"]
        assert "21" in w2["关联追踪"]
        assert w2["关联追踪"]["21"] == ["REV-20260901-02-21"]


class TestArchiveDashboard:
    """归档看板：每个窗口的可回查位置与尚待核验的副本。"""

    def test_dashboard_lists_locations_and_pending_copies(self, setup_data_dir):
        svc = fresh_service()
        dash = svc.archive_dashboard()
        locations = {w["窗口编号"]: w["保存位置"] for w in dash["windows"]}
        assert locations == {
            "WIN-20260901-01": "冷区",
            "WIN-20260901-02": "热区",
            "WIN-20260901-03": "热区",
        }
        # 尚待核验的副本
        assert {c["copy_id"] for c in dash["pending_copies"]} == {
            "CP-20260901-02-COLD", "CP-20260901-03-COLD",
        }
        # 待核修订单独入口
        assert [r["revision_id"] for r in dash["pending_revisions"]] == ["REV-20260901-02-21"]


class TestChecklist:
    """归档端从未完成范围生成下一轮校验清单。"""

    def test_checklist_excludes_verified_window(self, setup_data_dir):
        svc = fresh_service()
        checklist = svc.generate_checklist("2026-09-01")
        windows_in_items = {item["窗口编号"] for item in checklist["items"]}
        # 已通过校验的首窗不再排入
        assert "WIN-20260901-01" not in windows_in_items
        assert windows_in_items == {"WIN-20260901-02", "WIN-20260901-03"}
        assert checklist["excluded"] == [{"窗口编号": "WIN-20260901-01", "原因": "已通过冷档校验"}]

    def test_verification_failure_notes_window_and_copy(self, setup_data_dir):
        svc = fresh_service()
        result = svc.verify_copy("WIN-20260901-02", "CP-20260901-02-COLD", ok=False, note="校验值不匹配")
        record = result["record"]
        # 校验失败注明对应窗口及失败的副本
        assert record["窗口编号"] == "WIN-20260901-02"
        assert record["失败副本"] == "CP-20260901-02-COLD"
        assert record["ok"] is False
        assert result["cold_verified"] is False
        # 失败副本仍留在看板待核验列表
        dash = svc.archive_dashboard()
        assert any(c["copy_id"] == "CP-20260901-02-COLD" for c in dash["pending_copies"])

    def test_verified_window_drops_out_of_next_round(self, setup_data_dir):
        svc = fresh_service()
        result = svc.verify_copy("WIN-20260901-03", "CP-20260901-03-COLD", ok=True)
        assert result["cold_verified"] is True
        cov = svc.recallable_coverage("2026-09-01")
        w3 = next(w for w in cov["windows"] if w["窗口编号"] == "WIN-20260901-03")
        assert w3["已归档"] is True
        assert w3["保存位置"] == "冷区"
        # 下一轮清单只剩第二窗
        checklist = svc.generate_checklist("2026-09-01")
        assert {item["窗口编号"] for item in checklist["items"]} == {"WIN-20260901-02"}


class TestFirstRangeQueryThenRecompute:
    """验收流程：准备入口 → 读取前验收更正 → 完成范围查询 → 按返回索引重算。"""

    def test_prepare_accept_read_then_recompute(self, setup_data_dir):
        svc = fresh_service()
        # 首次可回查查询：入口已准备但尚未发给读者
        prepared = svc.prepare_range_query("2026-09-01")
        assert prepared["state"] == "已准备"
        assert prepared["delivered"] is False
        # 第二窗的更正值 21 在读取前正式验收
        accepted = svc.accept_revision("REV-20260901-02-21")
        assert accepted["revision"]["state"] == "已验收"
        # 此时完成第一次范围查询
        result = svc.read_range_query(prepared["query_id"])
        assert result["state"] == "已送达"
        # 从返回的索引定位第二窗
        idx = next(e for e in result["index"] if e["窗口编号"] == "WIN-20260901-02")
        assert idx["有效值"] == 21
        assert idx["存储位置"] == "热区"
        assert idx["已归档"] is False
        # 开始重算：核对实际采用值、保存位置与待核验窗口
        rec = svc.recompute(index_handle=idx["索引编号"])
        assert rec["adopted_value"] == 21
        assert rec["location"] == "热区"
        assert rec["archived"] is False
        assert rec["pending_windows"] == ["WIN-20260901-02", "WIN-20260901-03"]

    def test_recompute_before_acceptance_uses_confirmed_value(self, setup_data_dir):
        svc = fresh_service()
        # 未验收前：原确认值 20 仍可在热区读取
        rec = svc.recompute(window_id="WIN-20260901-02")
        assert rec["adopted_value"] == 20
        assert rec["location"] == "热区"
        assert rec["archived"] is False


class TestInterruptResume:
    """归档中断时按上次已确认的窗口继续重算。"""

    def test_resume_from_last_confirmed_window(self, setup_data_dir):
        svc = fresh_service()
        svc.interrupt_archive()
        rec = svc.recompute(resume=True)
        assert rec["archive_interrupted"] is True
        # 上次已确认窗口为首窗（唯一完成冷档校验的窗口）
        assert rec["resumed_from"] == "WIN-20260901-01"
        # 继续重算落到第二窗，未验收更正前采用原确认值 20
        assert rec["窗口编号"] == "WIN-20260901-02"
        assert rec["adopted_value"] == 20

    def test_resume_without_checkpoint_errors(self, setup_data_dir):
        svc = fresh_service()
        result = svc.recompute(resume=True)
        assert "error" in result


class TestSeriesIndexLocate:
    """序列索引以相同覆盖结果定位数据。"""

    def test_locate_matches_recallable_coverage(self, setup_data_dir):
        from app.services.series_index import SeriesIndexService
        svc = fresh_service()
        svc.accept_revision("REV-20260901-02-21")
        coverage = svc.recallable_coverage("2026-09-01")
        located = SeriesIndexService().locate_by_coverage("2026-09-01", coverage=coverage)
        assert located["coverage_count"] == 3
        by_window = {e["窗口编号"]: e for e in located["entries"]}
        assert by_window["WIN-20260901-01"]["存储位置"] == "冷区"
        assert by_window["WIN-20260901-01"]["已归档"] is True
        assert by_window["WIN-20260901-02"]["存储位置"] == "热区"
        assert by_window["WIN-20260901-02"]["有效值"] == 21
        assert by_window["WIN-20260901-02"]["已归档"] is False


class TestRecallRouter:
    """自定义路由先于通用资源路由匹配，且完整流程可走通。"""

    def test_recall_routes_registered_before_resource_router(self, setup_data_dir):
        from app.main import app
        paths: list[str] = []
        for route in app.routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                paths.extend(getattr(s, "path", "") for s in original.routes)
            else:
                paths.append(getattr(route, "path", ""))
        assert paths.index("/api/archive_recall/coverage") < paths.index("/api/{module}/{entry_id}")
        assert paths.index("/api/series_index/locate") < paths.index("/api/{module}/{entry_id}")

    def test_endpoint_flow_via_registered_services(self, setup_data_dir):
        from app.routers import recall
        from app.services import service_for
        from app.services.archive_recall import ArchiveRecallService
        import app.services as services_module

        # 用全新服务实例替换单例，保证用例间互不影响
        services_module.SERVICES["archive_recall"] = ArchiveRecallService()
        assert service_for("archive_recall") is services_module.SERVICES["archive_recall"]

        cov = recall.recallable_coverage(partition="2026-09-01")
        assert len(cov["windows"]) == 3

        prepared = recall.prepare_query(recall.PrepareQueryPayload(partition="2026-09-01"))
        assert prepared["state"] == "已准备"

        recall.accept_revision("REV-20260901-02-21")
        result = recall.read_query(prepared["query_id"])
        idx = next(e for e in result["index"] if e["窗口编号"] == "WIN-20260901-02")

        rec = recall.recompute(recall.RecomputePayload(index_handle=idx["索引编号"]))
        assert rec["adopted_value"] == 21
        assert rec["location"] == "热区"
        assert rec["archived"] is False
        assert rec["pending_windows"] == ["WIN-20260901-02", "WIN-20260901-03"]

        located = recall.locate_by_coverage(partition="2026-09-01")
        assert located["coverage_count"] == 3
