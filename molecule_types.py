"""
molecule_types.py — 分子类型定义、融合权重和转场配置
"""

MOLECULE_TYPES = {
    "hook_clash": {
        "name": "冲突钩子",
        "target_dur": 90,
        "min_clips": 10,
        "max_clips": 30,
        "clip_dur": (2.0, 5.0),
        "emo_min": 4,
        "events": ["冲突", "打斗", "威胁", "对峙"],
        "narrative_beats": {"hook": 0.4, "inciting": 0.4, "rising": 0.2},
        "bgm_vol": 0.35,
        "bgm_style": "tension_drums",
        "hook_rule": "0-3s必须是emo=5的冲突/打斗画面，不许有空镜",
        "grade": "eq=contrast=1.35:saturation=1.15:gamma=1.1",
    },
    "identity_twist": {
        "name": "身份反转",
        "target_dur": 100,
        "min_clips": 10,
        "max_clips": 35,
        "clip_dur": (2.0, 4.5),
        "emo_min": 3,
        "events": ["对峙", "跪地", "冲突"],
        "narrative_beats": {"setup": 0.2, "inciting": 0.3, "rising": 0.3, "climax": 0.2},
        "bgm_vol": 0.25,
        "bgm_style": "low_to_burst",
        "hook_rule": "前段低沉氛围(跪地/悲伤)，中段逐渐紧张，后段爆发(冲突/对峙)",
        "grade": "eq=contrast=1.2:saturation=1.05:gamma=1.05",
    },
    "emotional_resonance": {
        "name": "情感共鸣",
        "target_dur": 110,
        "min_clips": 10,
        "max_clips": 35,
        "clip_dur": (2.0, 5.0),
        "emo_min": 2,
        "events": ["悲伤", "日常"],
        "narrative_beats": {"setup": 0.3, "rising": 0.3, "climax": 0.2, "transition": 0.2},
        "bgm_vol": 0.15,
        "bgm_style": "sad_strings",
        "hook_rule": "用悲伤特写开场，留白0.5s再进BGM",
        "grade": "eq=contrast=1.1:saturation=0.9:gamma=1.02",
    },
    "quote_rhythm": {
        "name": "金句卡点",
        "target_dur": 90,
        "min_clips": 10,
        "max_clips": 30,
        "clip_dur": (1.5, 4.0),
        "emo_min": 2,
        "events": ["威胁", "对峙", "冲突"],
        "narrative_beats": {"inciting": 0.4, "rising": 0.3, "climax": 0.3},
        "bgm_vol": 0.4,
        "bgm_style": "epic_fire",
        "hook_rule": "每0.5s切一个画面卡BGM重拍，字幕加弹跳特效",
        "grade": "eq=contrast=1.4:saturation=1.2:gamma=1.1",
    },
    "cinematic_beauty": {
        "name": "光影美学",
        "target_dur": 60,
        "min_clips": 6,
        "max_clips": 12,
        "clip_dur": (3.0, 8.0),
        "emo_min": 2,
        "events": None,  # 不限事件
        "shots": ["全景", "特写"],
        "narrative_beats": {"setup": 0.4, "transition": 0.3, "rising": 0.3},
        "bgm_vol": 0.2,
        "bgm_style": "epic_orchestral",
        "hook_rule": "全景空镜+光影构图优先，节奏舒缓，留白多",
        "grade": "eq=contrast=1.15:saturation=1.0:gamma=1.02,colorbalance=rs=0.01:gs=-0.01:bs=-0.03",
    },
    "suspense_hook": {
        "name": "悬念钩子",
        "target_dur": 90,
        "min_clips": 10,
        "max_clips": 30,
        "clip_dur": (2.0, 5.0),
        "emo_min": 3,
        "events": ["冲突", "威胁", "对峙"],
        "narrative_beats": {"hook": 0.4, "inciting": 0.3, "rising": 0.3},
        "bgm_vol": 0.3,
        "bgm_style": "suspense",
        "hook_rule": "只剪冲突反转前3s，结尾必须悬崖断点——不给答案",
        "grade": "eq=contrast=1.3:saturation=1.1:gamma=1.08",
    },
}

FUSION_WEIGHTS = {
    "hook_clash":          {"molecular": 0.15, "aesthetic_score": 0.10, "hook_value": 0.45, "promo_value": 0.30},
    "identity_twist":      {"molecular": 0.10, "aesthetic_score": 0.25, "hook_value": 0.35, "promo_value": 0.30},
    "emotional_resonance": {"molecular": 0.10, "aesthetic_score": 0.40, "hook_value": 0.20, "promo_value": 0.30},
    "quote_rhythm":        {"molecular": 0.15, "aesthetic_score": 0.15, "hook_value": 0.25, "promo_value": 0.45},
    "cinematic_beauty":    {"molecular": 0.05, "aesthetic_score": 0.60, "hook_value": 0.10, "promo_value": 0.25},
    "suspense_hook":       {"molecular": 0.10, "aesthetic_score": 0.15, "hook_value": 0.50, "promo_value": 0.25},
}

TRANSITION_PROFILE_MAP = {
    "hook_clash": "fast_xfade",
    "identity_twist": "rise_then_burst",
    "emotional_resonance": "slow_fade",
    "quote_rhythm": "beat_cut",
    "cinematic_beauty": "soft_fade",
    "suspense_hook": "hard_cut_to_black",
}


# ── 共享纯函数（避免 selection_scorer ↔ selection_constraints 环依赖）──

def clip_event_text(c):
    v2 = c.get('_v2', {})
    return ' '.join(str(x) for x in [
        v2.get('event', ''), v2.get('event_subtype', ''),
        c.get('event_subtype', ''), ' '.join(c.get('scene_types', [])),
        c.get('desc_clean', '')
    ] if x)


def is_high_conflict_clip(c):
    event_text = clip_event_text(c)
    return (
        (c.get('emotion', 3) >= 5 or c.get('action_level', 1) >= 4) and
        any(k in event_text for k in ['冲突', '威胁', '打斗', '对峙', '怒吼', '抓扯', '持械', '奇幻爆发']) and
        c.get('visual_quality', 3) >= 3 and
        c.get('face_quality', '') not in ['无人', '模糊'] and
        c.get('event_conf', '') != '模糊' and
        c.get('usable', True)
    )


def molecular_score(c, mol_type):
    mdef = MOLECULE_TYPES[mol_type]
    event_text = clip_event_text(c)
    score = 0
    score += c.get('emotion', 3) * 12
    score += c.get('action_level', 1) * 10
    score += c.get('visual_quality', 3) * 8
    score += 10 if c.get('face_quality', '') in ['正脸', '侧脸', '半面'] else 0
    score += 8 if c.get('dialogue_visible') else 0
    score += min(10, len(c.get('subtitle_text', '') or '') // 2)
    speech_density = c.get('speech_density', 1)
    audio_energy = c.get('audio_energy', 1)
    transcript_excerpt = (c.get('transcript_excerpt', '') or '').strip()
    dialogue_anchor = c.get('dialogue_anchor', 'none')
    has_speech_peak = bool(c.get('has_speech_peak', False))
    beat_nearby = bool(c.get('beat_nearby', False))
    for target in mdef.get('events') or []:
        if target in event_text:
            score += 18
    for subtype in ['受伤', '倒地', '抓扯', '持械', '追逐', '怒吼', '哭泣', '奇幻爆发']:
        if subtype in event_text:
            score += 12
    if mol_type == 'quote_rhythm':
        score += speech_density * 8
        score += 16 if transcript_excerpt else 0
        score += 10 if dialogue_anchor in ['boundary', 'dense_speech'] else 0
    elif mol_type == 'identity_twist':
        score += speech_density * 6
        score += 12 if transcript_excerpt else 0
        score += 8 if c.get('dialogue_visible') else 0
    elif mol_type == 'emotional_resonance':
        score += audio_energy * 7
        score += 10 if transcript_excerpt else 0
    elif mol_type in ['hook_clash', 'suspense_hook']:
        score += audio_energy * 5
        score += 12 if has_speech_peak else 0
        score += 8 if beat_nearby else 0
    elif mol_type == 'cinematic_beauty':
        aes_score = c.get('aesthetic_score', 0)
        if aes_score > 0:
            score += aes_score * 15
        else:
            score += c.get('visual_quality', 3) * 10
        if c.get('_v2', {}).get('shot') in ['全景', '特写']:
            score += 15
        if c.get('face_quality', '') == '无人':
            score += 4
        score += min(4, audio_energy)
    aes_score = c.get('aesthetic_score', 0)
    if aes_score > 0 and mol_type != 'cinematic_beauty':
        score += aes_score * 5
    if c.get('reject_reason', '无') != '无' or not c.get('usable', True):
        score -= 100
    if c.get('event_conf', '') == '模糊':
        score -= 50
    return score
