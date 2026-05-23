"""
narrative_sequencer.py — 叙事序列优化，按故事阶段排序确保开头→发展→高潮
"""

# ── 每类分子的叙事流向（故事阶段序号顺序）──
# 序号对应 story_profile.json 中情节节点的出现顺序
NARRATIVE_FLOW = {
    "hook_clash":        [1, 2, 3, 4, 5],  # 标准三幕：序幕→升级→对局→深渊→终局
    "suspense_hook":     [1, 3, 4, 5, 2],  # 悬念：先抛冲突→回溯起因→再引爆
    "identity_twist":    [1, 3, 2, 4, 5],  # 身份反转：建立→揭穿→溯源→反击→收尾
    "emotional_resonance":[1, 3, 5, 2, 4],  # 情感：平静→波动→爆发→回忆→升华
    "quote_rhythm":      [1, 2, 3, 4, 5],  # 金句卡点：和冲突钩子同结构
    "cinematic_beauty":  [2, 4, 1, 3, 5],  # 美学：冲突画面优先→穿插安静→高潮
}


def narrative_stage_order(selected_clips, mol_type="hook_clash"):
    """按故事阶段排序：阶段间按叙事流顺序，阶段内按 hook_value 降序。"""
    if not selected_clips or len(selected_clips) < 3:
        return selected_clips

    flow = NARRATIVE_FLOW.get(mol_type, [1, 2, 3, 4, 5])
    stage_map = {}
    for i, c in enumerate(selected_clips):
        st = _get_stage_index(c)
        stage_map.setdefault(st, []).append((i, c))

    # 阶段内按 hook 降序
    for st in stage_map:
        stage_map[st].sort(key=lambda x: -x[1].get('hook_value', 1))

    # 按叙事流顺序输出
    result = []
    used = set()
    for stage_idx in flow:
        if stage_idx in stage_map:
            for orig_idx, c in stage_map[stage_idx]:
                if orig_idx not in used:
                    result.append(c)
                    used.add(orig_idx)

    # 兜底：补上遗漏的，也按阶段序排列
    remaining = []
    for i, c in enumerate(selected_clips):
        if i not in used:
            remaining.append((_get_stage_index(c), -c.get('hook_value', 1), i, c))
    remaining.sort()
    for _, _, _, c in remaining:
        result.append(c)

    # 开头 20% 强制从 flow[0] 阶段取至少 1 帧
    # 结尾 20% 强制从 flow[-1] 阶段取至少 1 帧
    n = len(result)
    if n >= 5:
        first_stage = flow[0]
        last_stage = flow[-1]
        has_first = any(_get_stage_index(c) == first_stage for c in result[:max(1, n//5)])
        has_last = any(_get_stage_index(c) == last_stage for c in result[-max(1, n//5):])
        # 如果开头/结尾缺失对应阶段帧，交换到正确位置
        if not has_first:
            for i, c in enumerate(result):
                if _get_stage_index(c) == first_stage and i > n//5:
                    result.insert(0, result.pop(i))
                    break
        if not has_last:
            for i in range(len(result)-1, -1, -1):
                if _get_stage_index(result[i]) == last_stage and i < n - n//5:
                    result.append(result.pop(i))
                    break

    return result


def narrative_order(selected_clips):
    """旧版叙事排序（兜底），委托到 narrative_stage_order。"""
    return narrative_stage_order(selected_clips, "hook_clash")


def _get_stage_index(clip):
    """从 clip 中提取故事阶段序号（1-5），无数据返回 0。"""
    st = (clip.get('story_stage', '') or clip.get('_v2', {}).get('story_stage', ''))
    if not st:
        ep = int(clip.get('ep', 0) or 0)
        if ep <= 10: return 1
        if ep <= 25: return 2
        if ep <= 45: return 3
        if ep <= 62: return 4
        if ep <= 76: return 5
        return 0
    stage_map = {"序幕": 1, "局势": 2, "权谋": 3, "生死": 4, "终局": 5}
    for k, v in stage_map.items():
        if k in st: return v
    return 0
