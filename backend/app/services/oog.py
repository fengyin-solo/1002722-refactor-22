"""超限箱管理业务规则：状态流转、字段校验与筛选口径都收在这里。

超限尺寸核算与专用吊具匹配不在本文件各写一份，统一委托给
:mod:`app.services.oog_rules`。三个入口（确认超限 / 安排作业 / 确认装机）
都调用同一份判定，保证同一只箱子在三处的结论一致；判定标准变更后调用
:meth:`OogService.recalculate_all` 即可重算全部历史记录。
"""
from __future__ import annotations

from typing import Any

from app.services.oog_rules import MEASURED_FIELD, apply_evaluation
from app.store import store

MODULE = "oog"
REQUIRED_FIELDS = ["超限箱号", "箱型尺寸", "超限方向"]
# 允许在动作里顺手上报的现场实测尺寸；不是必填，也不改变接口出入参结构。
OPTIONAL_FIELDS = [MEASURED_FIELD, "超限尺寸", "堆放区域", "绑扎方案"]
STATUS_ORDER = ["待确认", "已确认", "作业中", "已装机"]
ACTION_RULES = {"确认超限": "已确认", "安排作业": "作业中", "确认装机": "已装机"}
NEGATIVE_ACTIONS = []


class OogService:
    # 进程内只在首次用到本服务时，把历史记录统一重算一次到当前口径。
    _seed_recalculated = False

    def __init__(self) -> None:
        if not OogService._seed_recalculated:
            self.recalculate_all()
            OogService._seed_recalculated = True

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("超限箱号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 登记时若已带超限/实测尺寸或处置安排，一并收下，再交给统一口径判定。
        for field in OPTIONAL_FIELDS:
            if str(values.get(field) or "").strip():
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        apply_evaluation(entry)
        rows.append(entry)
        return entry, []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"超限箱 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于超限箱管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        # 现场可能在动作执行时补报实测尺寸或处置安排，先并入再走同一份判定。
        for field in OPTIONAL_FIELDS:
            if values is not None and str(values.get(field) or "").strip():
                entry[field] = values.get(field)
        apply_evaluation(entry)

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = bool(entry.get("abnormal"))
        return entry, f"超限箱已{action}"

    def recalculate_all(self) -> int:
        """判定标准变更后，用当前唯一口径重算全部超限记录。

        超限尺寸 / 专用吊具 / 超限状态一律刷新；堆放区域、绑扎方案缺失的
        按统一口径补齐，现场已有安排的保留。返回重算的记录条数。
        """
        count = 0
        for entry in store.rows(MODULE):
            apply_evaluation(entry)
            count += 1
        return count
