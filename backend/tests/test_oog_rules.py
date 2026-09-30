"""超限箱统一判据的归一性测试：三处入口一致、实测优先、标准变更重算、缺失补齐。

只依赖标准库，任意 Python 3.11+ 均可运行：
    cd backend && python3 -m unittest tests.test_oog_rules -v
"""
from __future__ import annotations

import unittest

from app.services import oog_rules


def make_entry(**values):
    entry = {
        "id": 1,
        "status": "待确认",
        "超限箱号": "OOGU-TEST",
        "箱型尺寸": "40GP",
        "超限方向": "待复核",
    }
    entry.update(values)
    return entry


class OogRulesTest(unittest.TestCase):
    def test_three_entries_share_one_verdict(self):
        """确认超限、安排作业、确认装机三个入口读到的结论必须完全相同。"""
        entry = make_entry(实测超长="380mm", 实测超宽="80mm")
        snapshots = []
        for _ in ("确认超限", "安排作业", "确认装机"):
            snapshots.append(oog_rules.apply_verdict(dict(entry)))
        for field in ("超限状态", "超限尺寸", "专用吊具", "堆放区域", "绑扎方案", "超限方向", "判定依据"):
            values = {snap[field] for snap in snapshots}
            self.assertEqual(len(values), 1, f"字段 {field} 在三处结论不一致")
        self.assertEqual(snapshots[0]["超限状态"], oog_rules.LEVEL_SEVERE)
        self.assertEqual(snapshots[0]["专用吊具"], "重型可调框架吊具（多向超限）")

    def test_measured_overrides_declared_per_direction(self):
        """两套判据冲突时逐方向以现场实测尺寸为准。"""
        entry = make_entry(
            申报超长="200", 申报超宽="220", 申报超高="90",
            实测超长="0", 实测超宽="0", 实测超高="0",
        )
        oog_rules.apply_verdict(entry)
        self.assertEqual(entry["超限状态"], oog_rules.LEVEL_NONE)
        self.assertEqual(entry["专用吊具"], "标准吊具")
        self.assertEqual(entry["超限方向"], "无超限")
        self.assertIn("实测", entry["判定依据"])

        # 混合来源：一个方向只有申报、另一个方向有实测，结论取各自的有效值。
        mixed = make_entry(申报超长="100", 实测超高="320")
        oog_rules.apply_verdict(mixed)
        self.assertEqual(mixed["超限状态"], oog_rules.LEVEL_SEVERE)
        self.assertEqual(mixed["超限方向"], "超长、超高")
        self.assertEqual(mixed["专用吊具"], "重型可调框架吊具（多向超限）")
        self.assertIn("超长100mm（申报）", mixed["超限尺寸"])
        self.assertIn("超高320mm（实测）", mixed["超限尺寸"])

    def test_thresholds_and_spreaders(self):
        cases = [
            ({"实测超长": "300"}, oog_rules.LEVEL_GENERAL, "加长吊具（超长梁）"),
            ({"实测超宽": "301"}, oog_rules.LEVEL_SEVERE, "重型加宽吊具（可调横梁）"),
            ({"实测超高": "260mm"}, oog_rules.LEVEL_GENERAL, "加高吊架"),
            ({"实测超长": "15cm", "实测超高": "120"}, oog_rules.LEVEL_GENERAL, "可调框架吊具（多向超限）"),
        ]
        for dims, level, spreader in cases:
            with self.subTest(dims=dims):
                entry = make_entry(**dims)
                oog_rules.apply_verdict(entry)
                self.assertEqual(entry["超限状态"], level)
                self.assertEqual(entry["专用吊具"], spreader)

    def test_fill_missing_uniformly_and_keep_manual(self):
        """堆放区域/绑扎方案缺失按统一方式补；人工填的值不能被覆盖。"""
        auto = make_entry(实测超高="400")
        oog_rules.apply_verdict(auto)
        self.assertEqual(auto["堆放区域"], "超限箱专区（严重超限位）")
        self.assertEqual(auto["绑扎方案"], "专项加固方案（需绑扎工程师审批）")
        self.assertIn("堆放区域", auto["系统补齐"])

        manual = make_entry(实测超高="400", 堆放区域="人工指定 B-06 贝", 绑扎方案="自备加固")
        oog_rules.apply_verdict(manual)
        self.assertEqual(manual["堆放区域"], "人工指定 B-06 贝")
        self.assertEqual(manual["绑扎方案"], "自备加固")
        self.assertNotIn("堆放区域", manual["系统补齐"])

    def test_rule_change_recomputes_existing_records(self):
        """判定标准变更抬版本后，历史记录按新标准重算，系统补齐项同步更新。"""
        old_version = oog_rules.RULE_VERSION
        old_limit = oog_rules.GENERAL_LIMIT_MM
        try:
            entry = make_entry(实测超高="260")
            oog_rules.apply_verdict(entry)
            self.assertEqual(entry["超限状态"], oog_rules.LEVEL_GENERAL)

            oog_rules.RULE_VERSION = "2099-01-01"
            oog_rules.GENERAL_LIMIT_MM = 100
            self.assertTrue(oog_rules.needs_recalc(entry))
            oog_rules.apply_verdict(entry)
            self.assertEqual(entry["超限状态"], oog_rules.LEVEL_SEVERE)
            self.assertEqual(entry["判定版本"], "2099-01-01")
            self.assertEqual(entry["堆放区域"], "超限箱专区（严重超限位）")
            self.assertTrue(entry["abnormal"])

            # recompute_all 只动版本落后的记录。
            fresh = make_entry(实测超高="50")
            oog_rules.apply_verdict(fresh)
            rows = [entry, fresh]
            self.assertEqual(oog_rules.recompute_all(rows), 0)
        finally:
            oog_rules.RULE_VERSION = old_version
            oog_rules.GENERAL_LIMIT_MM = old_limit

    def test_no_dimensions_placeholder(self):
        entry = make_entry()
        oog_rules.apply_verdict(entry)
        self.assertEqual(entry["超限状态"], oog_rules.LEVEL_NONE)
        self.assertEqual(entry["专用吊具"], "标准吊具")
        self.assertEqual(entry["堆放区域"], "普通箱区")
        self.assertTrue(entry["绑扎方案"].startswith("常规绑扎"))


if __name__ == "__main__":
    unittest.main()
