from __future__ import annotations

# The XLS specification separates evidence labels from the names shown for
# difficult configurations.  Both names are retained so the audit data can
# report the conversion without losing the original candidate evidence.
ALLOWED_TAGS = [
    "节奏",
    "延迟星星",
    "拆弹",
    "管子",
    "定位",
    "散打",
    "飞手",
    "防蹭",
    "留尾",
    "爆发",
    "底力",
    "交互",
    "轴交互",
    "爬梯交互",
    "定拍",
    "双押",
    "扫键",
    "死镰",
    "错位",
    "手速",
    "纵连",
    "跳拍",
    "如龙",
    "协调",
    "撞尾",
]

# Tags with a per-constant difficulty prevalence cap in the XLS before the
# requested sensitivity adjustment.
BASE_DIFFICULTY_CAPS: dict[str, float] = {
    "管子": 0.25,
    "定位": 0.25,
    "散打": 0.20,
    "飞手": 0.20,
    "防蹭": 0.15,
    "留尾": 0.15,
    "爆发": 0.20,
    "底力": 0.15,
    "交互": 0.10,
    "轴交互": 0.15,
    "爬梯交互": 0.15,
    "双押": 0.15,
    "扫键": 0.25,
    "错位_below_13_6": 0.30,
    "错位_at_least_13_6": 0.20,
    "手速": 0.30,
    "纵连": 0.25,
    "跳拍": 0.25,
    "拆弹": 0.25,
    "协调": 0.20,
}

# The current review permits a 75% sensitivity increase.  The adjustment is
# applied to the difficult-label prevalence ceilings, while the final output
# remains bounded by MAX_FINAL_TAGS below.
SENSITIVITY_MULTIPLIER = 1.75
DIFFICULTY_CAPS: dict[str, float] = {
    tag: min(1.0, cap * SENSITIVITY_MULTIPLIER)
    for tag, cap in BASE_DIFFICULTY_CAPS.items()
}

# Difficulty names from the XLS.  ``拆弹`` is the difficult form of the
# ``延迟星星`` candidate; it is intentionally a separate model label.
TAG_WEIGHTS: dict[str, float] = {
    "撞尾": 1.00,
    "死镰": 1.00,
    "如龙": 0.98,
    "留尾": 0.96,
    "协调": 0.90,
    "飞手": 0.88,
    "防蹭": 0.86,
    "轴交互": 0.84,
    "爬梯交互": 0.83,
    "拆弹": 0.82,
    "跳拍": 0.82,
    "纵连": 0.81,
    "管子": 0.80,
    "双押": 0.79,
    "交互": 0.76,
    "扫键": 0.75,
    "定位": 0.72,
    "错位": 0.72,
    "散打": 0.66,
    "节奏": 0.60,
    "定拍": 0.58,
    "爆发": 0.56,
    "手速": 0.42,
    "底力": 0.35,
    "延迟星星": 0.30,
}

MAX_FINAL_TAGS = 5
TAG_SCORE_RELATIVE_FLOOR = 0.32
TAG_SCORE_ABSOLUTE_FLOOR = 0.22

# These aliases are accepted only while reading old records or external
# evidence.  New annotations never emit the deprecated names.
TAG_ALIASES = {
    "手序": "协调",
    "拆谱": "协调",
    "拆譜": "协调",
    "左右分解": "协调",
    "分解配置": "协调",
    "秒划": "留尾",
    "秒划星星": "留尾",
    "秒画": "留尾",
    "秒畫": "留尾",
    "如龍": "如龙",
    "如龍掃": "如龙",
    "延迟星": "延迟星星",
    "延遲星星": "延迟星星",
    "延遲星": "延迟星星",
}

TARGET_LEVEL_INDEXES = [2, 3, 4]
# Training evidence is collected from 12.6-15.0, while the resulting local
# model is also allowed to annotate valid 12.0+ charts at runtime.
RUNTIME_MIN_DS = 12.0
TRAIN_MIN_DS = 12.6
MIN_TAG_DS = RUNTIME_MIN_DS
MAX_TAG_DS = 15.0
TAG_RULE_VERSION = 19
RULE_SPEC_SOURCE = "maimai.xls"
RULE_ENGINE = "local_xls_dual_model_consensus"

TAG_CATEGORIES = {
    "节奏": "节奏类配置",
    "延迟星星": "星星时序配置",
    "拆弹": "星星时序难点",
    "管子": "Hold短管/链式/异节奏Hold配置",
    "定位": "高密大位移/卡手/大跨度快星配置",
    "散打": "分散键位配置",
    "飞手": "大位移配置",
    "防蹭": "短星边界配置",
    "留尾": "Slide出张/秒划配置",
    "爆发": "局部爆发配置",
    "底力": "持续体力配置",
    "交互": "普通/大宇宙交互配置",
    "轴交互": "固定轴交互配置",
    "爬梯交互": "爬梯交互配置",
    "定拍": "锚定节拍配置",
    "双押": "局部同时击配置",
    "扫键": "同侧扫/短扫/转圈配置",
    "死镰": "对向Slide与反向Tap链配置",
    "错位": "隔拍Slide/双押引导错位配置",
    "手速": "速度类配置",
    "纵连": "纵向连续击打配置",
    "跳拍": "Swing/Shuffle/连续附点配置",
    "如龙": "引导换手的同侧扫配置",
    "协调": "手序/难协调键型配置",
    "撞尾": "Slide路径时序冲突配置",
}

# Generic labels are suppressed when several more recognizable difficulties
# already explain the chart.
GENERIC_TAGS = frozenset({"底力", "手速"})

DIFFICULTY_NAMES = {
    2: "Expert",
    3: "Master",
    4: "Re:Master",
}
