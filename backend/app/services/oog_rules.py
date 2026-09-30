"""超限箱统一判据：超限尺寸、专用吊具匹配、堆放区域与绑扎方案补全的唯一口径。

历史上「确认超限」按超限尺寸算一回、「安排作业」算一回、「确认装机」再算一回，
专用吊具匹配也各写各的，同一只箱子在三处会读出互相打架的结论。现在三个入口以及
列表/详情/导出都只调 apply_verdict() 这一份判据，结论必然一致。

判定口径（突出标准箱体外廓的量，单位毫米）：
- 任一方向突出量 > GENERAL_LIMIT_MM：严重超限；任一方向 > 0：一般超限；否则非超限。
- 申报尺寸与现场实测两套数据冲突时，每个方向都以现场实测为准。
- 专用吊具随超限等级与超限方向统一匹配。
- 堆放区域、绑扎方案仅在缺失时按统一规则补；人工已填的值保留，系统补过的值在
  判定标准变更后随重算更新。
判定标准调整时把 RULE_VERSION 抬一版，历史记录在下次读取或执行动作时自动重算。
"""
from __future__ import annotations

import re
from typing import Any

# 判据版本：标准变更后抬版，所有 判定版本 不一致的历史记录都会被重算。
RULE_VERSION = "2026-09-30"
GENERAL_LIMIT_MM = 300  # 突出量超过该毫米数即从一般超限升级为严重超限

# 方向：(内部名, 展示名, 申报字段, 实测字段)
DIRECTIONS = [
    ("长", "超长", "申报超长", "实测超长"),
    ("宽", "超宽", "申报超宽", "实测超宽"),
    ("高", "超高", "申报超高", "实测超高"),
]

FIELD_DIMENSION = "超限尺寸"
FIELD_DIRECTION = "超限方向"
FIELD_SPREADER = "专用吊具"
FIELD_AREA = "堆放区域"
FIELD_LASHING = "绑扎方案"
FIELD_VERDICT = "超限状态"
FIELD_REASON = "判定依据"
FIELD_RULE_VERSION = "判定版本"
FIELD_SYSTEM_FILLED = "系统补齐"

LEVEL_NONE = "非超限"
LEVEL_GENERAL = "一般超限"
LEVEL_SEVERE = "严重超限"

# 一般超限下单一方向对应的专用吊具；多方向同时超限时用可调框架吊具。
_SINGLE_SPREADER = {
    "超长": "加长吊具（超长梁）",
    "超宽": "加宽吊具（可调横梁）",
    "超高": "加高吊架",
}
_MULTI_SPREADER = "可调框架吊具（多向超限）"
_STANDARD_SPREADER = "标准吊具"

_AREA_BY_LEVEL = {
    LEVEL_SEVERE: "超限箱专区（严重超限位）",
    LEVEL_GENERAL: "超限箱专区（一般超限位）",
    LEVEL_NONE: "普通箱区",
}
_LASHING_BY_LEVEL = {
    LEVEL_SEVERE: "专项加固方案（需绑扎工程师审批）",
    LEVEL_GENERAL: "标准加固方案（四点绑扎）",
    LEVEL_NONE: "常规绑扎（免专项加固）",
}

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_mm(value: Any) -> int | None:
    """把「150」「150mm」「15cm」这类写法解析成毫米；解析不了视为缺失。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    match = _NUMBER_RE.search(str(value))
    if not match:
        return None
    mm = float(match.group())
    text = str(value).lower()
    if "cm" in text and "mm" not in text:
        mm *= 10
    elif re.search(r"\d(\.\d+)?\s*m(?!m)", text):
        mm *= 1000
    return int(round(mm))


def evaluate(entry: dict[str, Any]) -> dict[str, Any]:
    """按统一口径评估一只箱子，返回各方向突出量、超限等级与数据来源。

    实测与申报冲突时逐方向以现场实测为准：实测字段可解析（含 0）就覆盖申报值。
    """
    sides: list[dict[str, Any]] = []
    for _, label, declared_key, measured_key in DIRECTIONS:
        measured = parse_mm(entry.get(measured_key))
        declared = parse_mm(entry.get(declared_key))
        if measured is not None:
            value, source = measured, "现场实测"
        elif declared is not None:
            value, source = declared, "申报"
        else:
            value, source = None, None
        sides.append({
            "label": label,
            "value": value,
            "source": source,
            "measured": measured,
            "declared": declared,
        })

    known = [side for side in sides if side["value"] is not None]
    active = [side for side in known if side["value"] > 0]
    max_value = max((side["value"] for side in active), default=0)
    if not active:
        level = LEVEL_NONE
    elif max_value > GENERAL_LIMIT_MM:
        level = LEVEL_SEVERE
    else:
        level = LEVEL_GENERAL
    has_measured = any(side["measured"] is not None for side in sides)
    has_declared = any(side["declared"] is not None for side in sides)
    return {
        "sides": sides,
        "known": known,
        "active": active,
        "max_value": max_value,
        "level": level,
        "is_oversize": bool(active),
        "has_measured": has_measured,
        "has_declared": has_declared,
    }


def match_spreader(result: dict[str, Any]) -> str:
    """专用吊具匹配：随超限等级与超限方向统一出结论，不再各入口各写一份。"""
    if not result["active"]:
        return _STANDARD_SPREADER
    labels = [side["label"] for side in result["active"]]
    if len(labels) == 1:
        spreader = _SINGLE_SPREADER[labels[0]]
    else:
        spreader = _MULTI_SPREADER
    if result["level"] == LEVEL_SEVERE:
        spreader = f"重型{spreader}"
    return spreader


def _source_summary(result: dict[str, Any]) -> str:
    if result["has_measured"] and result["has_declared"]:
        return "现场实测（与申报冲突处以实测为准）"
    if result["has_measured"]:
        return "现场实测"
    if result["has_declared"]:
        return "申报尺寸"
    return "—"


def _source_tag(result: dict[str, Any]) -> str:
    """超限尺寸列里挂的来源标签：实测/申报；无数据时不挂。"""
    active = result["active"]
    if not active:
        return ""
    if all(side["source"] == "现场实测" for side in active):
        return "实测"
    if all(side["source"] == "申报" for side in active):
        return "申报"
    return ""


def _describe_dimensions(result: dict[str, Any]) -> str:
    """超限尺寸列文案：给出各超限方向的突出量与来源。"""
    active = result["active"]
    if not active:
        return f"未超限，依据{_source_summary(result)}"
    parts = [
        f"{side['label']}{side['value']}mm"
        + ("（实测）" if side["source"] == "现场实测" else "（申报）")
        if _source_tag(result) == "" else f"{side['label']}{side['value']}mm"
        for side in active
    ]
    text = "、".join(parts)
    tag = _source_tag(result)
    return f"{text}（{tag}）" if tag else text


def _describe_reason(result: dict[str, Any]) -> str:
    source = _source_summary(result)
    if not result["known"]:
        return "缺少申报/实测超限尺寸，暂按非超限占位，待现场实测后重算"
    if result["active"]:
        sides = "、".join(f"{s['label']}{s['value']}mm" for s in result["active"])
        return (
            f"依据{source}：{sides}，最大突出{result['max_value']}mm，"
            f"判为{result['level']}"
        )
    return f"依据{source}：各方向突出量均不大于0mm，判为非超限"


def _fill_if_missing(
    entry: dict[str, Any],
    field: str,
    uniform_value: str,
    system_filled: list[str],
) -> None:
    """缺失就按统一规则补；系统以前补过的随本次判定一起重算，人工填的一律保留。"""
    current = str(entry.get(field) or "").strip()
    if not current or field in system_filled:
        entry[field] = uniform_value
        if field not in system_filled:
            system_filled.append(field)


def apply_verdict(entry: dict[str, Any]) -> dict[str, Any]:
    """把统一判据落到一条超限箱记录上（就地更新并返回），三个入口共用这一份。"""
    result = evaluate(entry)
    active_labels = [side["label"] for side in result["active"]]

    entry[FIELD_DIMENSION] = _describe_dimensions(result)
    # 有尺寸数据时超限方向以判据为准；完全没有尺寸时保留登记时填的原始方向。
    if result["known"]:
        entry[FIELD_DIRECTION] = "、".join(active_labels) if active_labels else "无超限"
    entry[FIELD_SPREADER] = match_spreader(result)
    entry[FIELD_VERDICT] = result["level"]
    entry[FIELD_REASON] = _describe_reason(result)

    system_filled = [str(name) for name in entry.get(FIELD_SYSTEM_FILLED, [])]
    _fill_if_missing(entry, FIELD_AREA, _AREA_BY_LEVEL[result["level"]], system_filled)
    _fill_if_missing(entry, FIELD_LASHING, _LASHING_BY_LEVEL[result["level"]], system_filled)
    entry[FIELD_SYSTEM_FILLED] = system_filled

    entry[FIELD_RULE_VERSION] = RULE_VERSION
    # 严重超限在看板上计入异常量，异常口径同样跟着这份判据走。
    entry["abnormal"] = result["level"] == LEVEL_SEVERE
    return entry


def needs_recalc(entry: dict[str, Any]) -> bool:
    """判据版本落后（或从未判定过）的记录需要按新标准重算。"""
    return entry.get(FIELD_RULE_VERSION) != RULE_VERSION


def recompute_all(rows: list[dict[str, Any]]) -> int:
    """重算一批超限记录，返回实际重算条数；判定标准变更后用于补齐历史数据。"""
    count = 0
    for row in rows:
        if needs_recalc(row):
            apply_verdict(row)
            count += 1
    return count
