from __future__ import annotations


def rating_band_label(rating: int) -> str:
    """dx2026 国服常用分段标签（锐评用，非官方段位）。"""
    if rating >= 16500:
        return "顶段"
    if rating >= 16000:
        return "超高分"
    if rating >= 15500:
        return "高分"
    if rating >= 15000:
        return "中高分"
    if rating >= 14500:
        return "中坚"
    if rating >= 14000:
        return "成长"
    if rating >= 13000:
        return "进阶"
    return "积累"


def rating_band_hint(rating: int) -> str:
    if rating >= 16500:
        return "顶段：B50 容错极低，水谱地板、未满鸟吃分都是硬伤，优先拷打结构纯度。"
    if rating >= 16000:
        return "超高分：低含金量与未满鸟撑分要重点喷；能留在 B50 的低达成通常是高定上限或债。"
    if rating >= 15500:
        return "高分：应减少靠未满鸟硬蹭的谱；高定低达成=潜力与债并存。"
    if rating >= 15000:
        return "中高分：未满鸟高定可当上限尝试，但地板过多说明基本盘不稳。"
    if rating >= 14500:
        return "中坚：高定未满鸟可看作摸上限；要分清潜力股和纯蹭定数。"
    if rating >= 14000:
        return "成长：结构优先于单曲玄学，指出该补鸟还是该拔高。"
    if rating >= 13000:
        return "进阶：B50 完整度与鸟率比极限挖分更重要。"
    return "积累：先看基本盘和版本适应，未满鸟进 B50 多半是上限尝试。"
