"""超限箱判定的唯一口径。

超限尺寸核算、超限等级、专用吊具匹配、堆放区域与绑扎方案全部在这一个文件里产出，
三个入口（确认超限 / 安排作业 / 确认装机）以及「重算已有超限记录」都只允许调用
:func:`evaluate`，任何入口都不再各写一份阈值或吊具匹配规则，保证同一只箱子在
三处读到的结论完全一致。

口径要点：

- 现场实测尺寸优先：登记的理论超限尺寸与现场实测尺寸冲突时，一律以实测为准。
- 超限尺寸 / 专用吊具 / 超限状态属于「判定结论」，每次计算都以本模块为准并覆盖。
- 堆放区域 / 绑扎方案属于「处置安排」，已有值保留，缺失时才按统一口径补齐。
- 判定标准只改这一个文件；改完调用重算即可让全部历史记录刷新到新结论。
"""
from __future__ import annotations

import re
from typing import Any

# 标准集装箱外形尺寸（长、宽、高，毫米），用于由实测外廓尺寸反算超出量。
STANDARD_SIZE_MM: dict[str, tuple[int, int, int]] = {
    "20": (6096, 2438, 2591),
    "40": (12192, 2438, 2591),
    "45": (13716, 2438, 2896),
}

# 方向的固定排列顺序，保证「长/宽/高」在各处输出顺序一致。
DIRECTION_ORDER = ("长", "宽", "高")

# 方位词到长/宽/高三个超限方向的归并（左右归宽、前后归长、上顶归高）。
SIDE_TO_DIRECTION = {
    "长": "长", "前": "长", "后": "长",
    "宽": "宽", "左": "宽", "右": "宽",
    "高": "高", "上": "高", "顶": "高",
}

# 各方向超出标准尺寸多少即判定超限（厘米）；0 表示只要超出就算超限。
OOG_LIMIT_CM = {"长": 0, "宽": 0, "高": 0}

# 超限等级按「各方向中最大单侧超出量（厘米）」分档，顺序即从严到宽的匹配顺序。
TIER_BY_MAX_EXCESS = (
    ("超级超限", 60),
    ("二级超限", 30),
    ("一级超限", 0),
)

# 单一方向超限时配的专用吊具。
SPREADER_BY_DIRECTION = {"长": "加长吊梁", "宽": "加宽吊梁", "高": "超高吊架"}

# 各超限等级默认堆放区域。
STACK_AREA_BY_TIER = {
    "一级超限": "超限堆箱区-一级贝位",
    "二级超限": "超限堆箱区-二级贝位",
    "超级超限": "超限堆箱区-超级贝位（独立隔离）",
}

# 各超限等级默认绑扎方案。
LASHING_BY_TIER = {
    "一级超限": "常规加固绑扎（四角绑扎）",
    "二级超限": "加强绑扎（方木支撑加多道绑扎）",
    "超级超限": "专项绑扎方案（重型支撑+多道绑扎，需专项审批）",
}

# 现场实测尺寸字段名；登记口径写在「超限尺寸」，实测口径写在「实测尺寸」。
MEASURED_FIELD = "实测尺寸"
DECLARED_FIELD = "超限尺寸"
DIRECTION_FIELD = "超限方向"

# 判定结论统一写回这几个既有字段，接口出入参不变（不新增字段）。
DERIVED_FIELDS = ("超限尺寸", "专用吊具", "堆放区域", "绑扎方案", "超限状态")

_NUMBER = r"(\d+(?:\.\d+)?)"
_PROTRUSION_RE = re.compile(
    r"(左|右|前|后|上|顶|长|宽|高)\D{0,4}?" + _NUMBER + r"\s*(cm|厘米|mm|毫米)?"
)
_SIZE_CODE_RE = re.compile(r"(?:^|\D)(45|40|20)\s*(?:尺|GP|gp|英尺)?(?:\D|$)")
_DIMENSION_RE = {
    axis: re.compile(axis + r"\D{0,4}?" + _NUMBER + r"\s*(mm|毫米|cm|厘米|m|米)?")
    for axis in DIRECTION_ORDER
}


def _to_cm(value: float, unit: str | None) -> float:
    """把解析到的数值统一换算成厘米；凸出量默认按厘米，外廓尺寸默认按毫米。"""
    if unit in ("mm", "毫米"):
        return value / 10.0
    if unit in ("m", "米"):
        return value * 100.0
    return value


def parse_size_code(text: Any) -> str | None:
    """从「箱型尺寸」文本里识别 20/40/45 尺箱型。"""
    match = _SIZE_CODE_RE.search(str(text or ""))
    return match.group(1) if match else None


def parse_protrusion(text: Any) -> dict[str, float]:
    """解析「左超25cm/上超40」这类凸出量描述，归并成长/宽/高三个方向的厘米数。

    同方向多个方位（如左、右）取较大值。
    """
    excess: dict[str, float] = {}
    for side, raw_value, unit in _PROTRUSION_RE.findall(str(text or "")):
        direction = SIDE_TO_DIRECTION[side]
        value = _to_cm(float(raw_value), unit)
        excess[direction] = max(excess.get(direction, 0.0), value)
    return excess


def parse_overall_dimensions(text: Any) -> dict[str, int] | None:
    """解析「12192×2438×2680」或「长12192 宽2438 高2680」的实测外廓尺寸（毫米）。

    带单位时按单位换算成毫米；纯数字按毫米处理。
    """
    raw = str(text or "")

    dims: dict[str, int] = {}
    for axis, pattern in _DIMENSION_RE.items():
        match = pattern.search(raw)
        if match:
            value = float(match.group(1))
            unit = match.group(2)
            if unit in ("cm", "厘米"):
                value *= 10
            elif unit in ("m", "米"):
                value *= 1000
            dims[axis] = int(round(value))
    if len(dims) == 3:
        return dims

    numbers = [float(part) for part in re.findall(r"\d+(?:\.\d+)?", raw)]
    if len(numbers) >= 3:
        long_, wide, high = numbers[:3]
        return {"长": int(round(long_)), "宽": int(round(wide)), "高": int(round(high))}
    return None


def _excess_from_overall(text: Any, size_code: str | None) -> dict[str, float]:
    """由实测外廓尺寸与标准箱外形之差，反算各方向超出量（厘米）。"""
    if not size_code:
        return {}
    overall = parse_overall_dimensions(text)
    if overall is None:
        return {}
    standard = STANDARD_SIZE_MM[size_code]
    excess: dict[str, float] = {}
    for axis, std in zip(DIRECTION_ORDER, standard):
        delta_cm = (overall.get(axis, 0) - std) / 10.0
        if delta_cm > OOG_LIMIT_CM[axis]:
            excess[axis] = round(delta_cm)
    return excess


def parse_direction_hints(text: Any) -> list[str]:
    """从「超限方向」自由文本（如「左侧、顶部」）归并出长/宽/高方向。"""
    raw = str(text or "")
    found = {SIDE_TO_DIRECTION[token] for token in SIDE_TO_DIRECTION if token in raw}
    return [axis for axis in DIRECTION_ORDER if axis in found]


# 单侧凸出量的合理上限（厘米）：超过这个值说明读到的是外廓尺寸而非凸出量。
_PLAUSIBLE_PROTRUSION_CM = 500.0


def resolve_excess(text: Any, size_code: str | None) -> dict[str, float]:
    """从一段尺寸描述统一解析各方向超出量（厘米）。

    优先按凸出量（左超/上超）解析；若数值明显是外廓尺寸（如长12192），
    改按实测外廓尺寸减标准箱外形反算。
    """
    raw = str(text or "").strip()
    if not raw:
        return {}
    protrusion = parse_protrusion(raw)
    if protrusion and max(protrusion.values()) <= _PLAUSIBLE_PROTRUSION_CM:
        return protrusion
    return _excess_from_overall(raw, size_code)



def _tier_for(max_excess: float) -> str:
    for tier, threshold in TIER_BY_MAX_EXCESS:
        if max_excess > threshold:
            return tier
    return ""


def _spreader_for(directions: list[str], tier: str) -> str:
    if not directions:
        return "标准吊具"
    # 多方向同时超限，或超级超限，统一配重型组合吊具。
    if len(directions) > 1 or tier == "超级超限":
        return "重型组合吊具"
    return SPREADER_BY_DIRECTION[directions[0]]


def _canonical_size_text(excess: dict[str, float], directions: list[str], numeric: bool) -> str:
    if not directions:
        return "无超限"
    if not numeric:
        return "按登记方向计（实测尺寸待补）"
    return "、".join(f"{axis}超{int(excess[axis])}cm" for axis in directions)


def evaluate(entry: dict[str, Any]) -> dict[str, Any]:
    """对一只超限箱执行唯一一份判定，返回需要写回的字段值。

    取数顺序：现场实测尺寸 > 登记的超限尺寸。两套口径冲突时以现场实测为准。
    返回的 dict 只包含既有字段名，接口出入参不变。
    """
    size_code = parse_size_code(entry.get("箱型尺寸"))

    measured = entry.get(MEASURED_FIELD)
    declared = entry.get(DECLARED_FIELD)

    excess: dict[str, float] = {}
    basis = ""
    has_measured = bool(str(measured or "").strip())
    if has_measured:
        # 现场实测尺寸优先：实测可能给凸出量，也可能给外廓尺寸，两种都认。
        # 只要提供了实测，即使解析下来不超限，也不再回退登记口径（冲突以实测为准）。
        excess = resolve_excess(measured, size_code)
        basis = "现场实测尺寸"
    elif str(declared or "").strip():
        excess = resolve_excess(declared, size_code)
        if excess:
            basis = "登记超限尺寸"

    numeric = bool(excess)
    directions = [axis for axis in DIRECTION_ORDER if excess.get(axis, 0) > 0]
    if not directions:
        directions = parse_direction_hints(entry.get(DIRECTION_FIELD))
        if directions and not basis:
            basis = "登记超限方向（实测待补）"

    is_oog = bool(directions)
    max_excess = max((excess.get(axis, 0.0) for axis in directions), default=0.0)
    tier = _tier_for(max_excess) if numeric else ("一级超限" if is_oog else "")

    size_text = _canonical_size_text(excess, directions, numeric)
    status_text = f"{tier}（依据{basis}）" if is_oog else f"未超限（依据{basis or '标准箱型'}）"

    derived = {
        "超限尺寸": size_text,
        "专用吊具": _spreader_for(directions, tier),
        "超限状态": status_text,
        "堆放区域": STACK_AREA_BY_TIER.get(tier, "普通堆箱区"),
        "绑扎方案": LASHING_BY_TIER.get(tier, "标准绑扎"),
    }
    derived["_is_oog"] = is_oog
    return derived


def apply_evaluation(entry: dict[str, Any]) -> dict[str, Any]:
    """把统一判定写回记录。

    超限尺寸 / 专用吊具 / 超限状态是判定结论，无条件以本口径覆盖；
    堆放区域 / 绑扎方案是处置安排，现场已有安排的保留，缺失才按统一口径补齐。
    """
    derived = evaluate(entry)
    for field in ("超限尺寸", "专用吊具", "超限状态"):
        entry[field] = derived[field]
    for field in ("堆放区域", "绑扎方案"):
        if not str(entry.get(field) or "").strip():
            entry[field] = derived[field]
    entry["abnormal"] = derived["_is_oog"]
    return entry
