"""超限箱管理业务规则：状态流转、字段校验、筛选口径，以及统一判据的入口。

确认超限、安排作业、确认装机三个动作都不各自再算一遍，一律调用
oog_rules.apply_verdict()；列表、详情、导出读到的也是同一份判定结果。
"""
from __future__ import annotations

from typing import Any

from app.services import oog_rules
from app.store import store

MODULE = "oog"
REQUIRED_FIELDS = ["超限箱号", "箱型尺寸", "超限方向"]
STATUS_ORDER = ["待确认", "已确认", "作业中", "已装机"]
ACTION_RULES = {"确认超限": "已确认", "安排作业": "作业中", "确认装机": "已装机"}
NEGATIVE_ACTIONS = []

# 登记时允许透传的输入字段（含统一判据要用的申报/实测突出量）。
_ACCEPT_FIELDS = [
    "超限箱号", "箱型尺寸", "超限方向",
    "申报超长", "申报超宽", "申报超高",
    "实测超长", "实测超宽", "实测超高",
    "堆放区域", "绑扎方案",
]


class OogService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        # 看板/列表的超限结论必须跟当前判据一致：标准变更后先把历史记录补齐。
        self.recalculate()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("超限箱号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            # 单个入口（确认装机等）读到的结论同样不能落后于判据。
            oog_rules.apply_verdict(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in _ACCEPT_FIELDS:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        # 登记即按统一判据落一遍结论，三个动作后续只流转状态、重算判据。
        oog_rules.apply_verdict(entry)
        return entry, []

    def recalculate(self) -> int:
        """判定标准变更后重算全部超限记录，堆放区域/绑扎方案缺的按统一方式补。"""
        return oog_rules.recompute_all(store.rows(MODULE))

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"超限箱 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于超限箱管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        # 确认超限 / 安排作业 / 确认装机：状态各自流转，超限结论同一份判据重算。
        oog_rules.apply_verdict(entry)
        return entry, f"超限箱已{action}"
