"""
run_promo_molecular.py — Phase 5: 分子级拆解宣发

基于行业最佳实践的6种"分子"维度，每种生成1条独立宣发素材。
复用 genre_engine 的 multi_pass_rank + enforce_narrative_diversity，
但每条分子有独立的选片策略、BGM风格和节奏设计。

6种分子类型:
  1. 冲突钩子 (hook_clash)  — 15s, emo≥5冲突画面, 紧张鼓点
  2. 身份反转 (identity_twist) — 15-20s, 对峙+跪地+冲突combo, 低沉→爆发
  3. 情感共鸣 (emotional_resonance) — 20-30s, 悲伤+日常emo≥4, 悲伤弦乐
  4. 金句卡点 (quote_rhythm) — 10-15s, 威胁+对峙+张力, 燃向BGM
  5. 光影美学 (cinematic_beauty) — 15-20s, 全景/特写+不同时空, 史诗配乐
  6. 悬念钩子 (suspense_hook) — 12-15s, 冲突emo=5截取反转前, 悬念音效

每条视频遵循三段式节奏: 0-3s冲突爆发 → 3-10s预告拼接 → 10-15s悬崖断点

用法:
  python run_promo_molecular.py                    # 生成全部6条
  python run_promo_molecular.py --type hook_clash   # 只生成指定类型
  python run_promo_molecular.py --dry-run            # 仅展示选片，不切割
"""
import sys, os, json, time, subprocess, re, csv
from pathlib import Path

# ── Path setup ──
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

from edit_utils import (
    parse_vision_line,
    parallel_cut_clips,
    check_audio_quality,
    fact_check_selection,
    load_engine_config,
    load_project_config,
    episode_filename,
    write_json,
)
from genre_engine import (_genre_detect_two_pass, multi_pass_rank,
                          EDIT_DIRECTIONS,
                          recommend_directions, apply_direction_weights,
                          prefilter_for_direction, enforce_narrative_diversity,
                          tag_narrative_function)

# ── Config ──
_cfg = load_engine_config()
_project = load_project_config(_cfg)
_render_cfg = (_cfg.get("render") or {}) if isinstance(_cfg.get("render"), dict) else {}
SOURCE_DIR = _project["media_dir"]
ANALYSIS_FILE = _project["analysis_v3"]
ANALYSIS_FALLBACK = _project["analysis_fallback"]
OUTPUT_DIR = _project["molecular_dir"]
BGM_FILE = _project["bgm"]
PROJECT_NAME = _project["project_name"]
EPISODE_NAME_TEMPLATE = _project["episode_name_template"]
FFMPEG = _cfg.get("ffmpeg", "ffmpeg")
FFPROBE = _cfg.get("ffprobe", "ffprobe")


def name_func(ep):
    return episode_filename(ep, EPISODE_NAME_TEMPLATE)


def molecule_output_path(mol_type):
    suffix_map = {
        "hook_clash": "冲突钩子",
        "identity_twist": "身份反转",
        "emotional_resonance": "情感共鸣",
        "quote_rhythm": "金句卡点",
        "cinematic_beauty": "光影美学",
        "suspense_hook": "悬念钩子",
    }
    label = suffix_map.get(mol_type, mol_type)
    return os.path.join(OUTPUT_DIR, f"{PROJECT_NAME}_宣发_{label}.mp4")

# ── 分子类型定义 ──
MOLECULE_TYPES = {
    "hook_clash": {
        "name": "冲突钩子",
        "target_dur": 15,
        "min_clips": 3,
        "max_clips": 5,
        "clip_dur": (1.5, 3.0),
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
        "target_dur": 18,
        "min_clips": 4,
        "max_clips": 6,
        "clip_dur": (1.5, 3.5),
        "emo_min": 4,
        "events": ["对峙", "跪地", "冲突"],
        "narrative_beats": {"setup": 0.2, "inciting": 0.3, "rising": 0.3, "climax": 0.2},
        "bgm_vol": 0.25,
        "bgm_style": "low_to_burst",
        "hook_rule": "前段低沉氛围(跪地/悲伤)，中段逐渐紧张，后段爆发(冲突/对峙)",
        "grade": "eq=contrast=1.2:saturation=1.05:gamma=1.05",
    },
    "emotional_resonance": {
        "name": "情感共鸣",
        "target_dur": 25,
        "min_clips": 4,
        "max_clips": 7,
        "clip_dur": (2.0, 4.0),
        "emo_min": 4,
        "events": ["悲伤", "日常"],
        "narrative_beats": {"setup": 0.3, "rising": 0.3, "climax": 0.2, "transition": 0.2},
        "bgm_vol": 0.15,
        "bgm_style": "sad_strings",
        "hook_rule": "用悲伤特写开场，留白0.5s再进BGM",
        "grade": "eq=contrast=1.1:saturation=0.9:gamma=1.02",
    },
    "quote_rhythm": {
        "name": "金句卡点",
        "target_dur": 12,
        "min_clips": 3,
        "max_clips": 5,
        "clip_dur": (0.8, 2.0),
        "emo_min": 4,
        "events": ["威胁", "对峙", "冲突"],
        "narrative_beats": {"inciting": 0.4, "rising": 0.3, "climax": 0.3},
        "bgm_vol": 0.4,
        "bgm_style": "epic_fire",
        "hook_rule": "每0.5s切一个画面卡BGM重拍，字幕加弹跳特效",
        "grade": "eq=contrast=1.4:saturation=1.2:gamma=1.1",
    },
    "cinematic_beauty": {
        "name": "光影美学",
        "target_dur": 18,
        "min_clips": 4,
        "max_clips": 6,
        "clip_dur": (2.0, 3.5),
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
        "target_dur": 13,
        "min_clips": 3,
        "max_clips": 4,
        "clip_dur": (1.5, 2.5),
        "emo_min": 4,
        "events": ["冲突", "威胁", "对峙"],
        "narrative_beats": {"hook": 0.4, "inciting": 0.3, "rising": 0.3},
        "bgm_vol": 0.3,
        "bgm_style": "suspense",
        "hook_rule": "只剪冲突反转前3s，结尾必须悬崖断点——不给答案",
        "grade": "eq=contrast=1.3:saturation=1.1:gamma=1.08",
    },
}


def _parse_analysis_file(filepath):
    """解析单文件 → clips列表"""
    clips = []
    if not os.path.exists(filepath):
        return clips
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(' ', 2)
            if len(parts) < 3:
                continue
            ep_str = parts[0].replace('ep', '').split('_')[0]  # 兼容 "ep01_f1" 格式
            time_str = parts[1].replace('s', '')
            desc = parts[2]
            parsed = parse_vision_line(desc)
            parsed["ep"] = ep_str
            try:
                parsed["time"] = int(time_str) if time_str.isdigit() else 0
            except:
                parsed["time"] = 0
            parsed["desc"] = desc
            clips.append(parsed)
    return clips


def load_analysis():
    """加载审片数据 — V1+V2合并，V2覆盖同(ep,time)条目"""
    clips = []
    v1_clips = _parse_analysis_file(ANALYSIS_FALLBACK)
    v2_clips = _parse_analysis_file(ANALYSIS_FILE)

    # 建立V2的 (ep, time) 覆盖集合
    v2_keys = set()
    for c in v2_clips:
        v2_keys.add((int(c.get('ep', '0') or 0), c.get('time', 0)))

    # V2数据优先加入
    clips.extend(v2_clips)

    # V1数据仅保留V2未覆盖的集（去重：同ep且时间差<10s）
    v1_added = 0
    for c in v1_clips:
        ep = int(c.get('ep', '0') or 0)
        ts = c.get('time', 0)
        is_dup = False
        for v2_ep, v2_ts in v2_keys:
            if v2_ep == ep and abs(v2_ts - ts) < 10:
                is_dup = True
                break
        if not is_dup:
            clips.append(c)
            v1_added += 1

    # 为 legacy 条目补齐通用字段，避免只依赖 V2
    for c in clips:
        c.setdefault('usable', True)
        c.setdefault('reject_reason', '无')
        c.setdefault('event_conf', '可见')
        c.setdefault('visual_quality', 3)
        c.setdefault('face_quality', '正脸' if c.get('faces', 0) >= 1 else '模糊')
        c.setdefault('action_level', min(5, max(1, c.get('emotion', 3))))
        c.setdefault('dialogue_visible', c.get('dialogue_lines', 0) > 0)
        c.setdefault('subtitle_text', '')
        c.setdefault('event_subtype', c.get('event_subtype', '无') or '无')

    v2_eps = len(set(int(c.get('ep', '0') or 0) for c in v2_clips))
    v1_eps = len(set(int(c.get('ep', '0') or 0) for c in v1_clips))
    all_eps = len(set(int(c.get('ep', '0') or 0) for c in clips))
    print(f"  数据合并: V2 {len(v2_clips)}条({v2_eps}集) + V1+{v1_added}条({v1_eps}集) → {len(clips)}条({all_eps}集)")
    return clips


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
        score += c.get('visual_quality', 3) * 10
        if c.get('_v2', {}).get('shot') in ['全景', '特写']:
            score += 15
        if c.get('face_quality', '') == '无人':
            score += 4
        score += min(4, audio_energy)
    if c.get('reject_reason', '无') != '无' or not c.get('usable', True):
        score -= 100
    if c.get('event_conf', '') == '模糊':
        score -= 50
    return score


def _is_legacy_data(clips):
    """检测数据集是否为 legacy（缺少 V2 音频字段）。
    如果超过 80% 的条目没有 audio_energy 字段，视为 legacy。"""
    if not clips:
        return True
    has_audio = sum(1 for c in clips if c.get('audio_energy', 0) > 1 or c.get('speech_density', 0) > 1)
    return has_audio < len(clips) * 0.2


def _molecular_filter_pass(clips, mol_type, strict=True):
    """单次过滤。strict=True 为正常模式，strict=False 为降级模式（放宽阈值）。"""
    mdef = MOLECULE_TYPES[mol_type]
    pool = []

    # 降级模式：emotion 阈值降低 1 级，跳过事件强匹配和人脸要求
    emo_min = mdef.get('emo_min', 3)
    if not strict:
        emo_min = max(2, emo_min - 1)

    for c in clips:
        emo = c.get('emotion', 3)
        if emo < emo_min:
            continue
        if c.get('usable') is False:
            continue
        if c.get('reject_reason', '无') != '无':
            continue
        if c.get('event_conf', '') == '模糊' and c.get('visual_quality', 3) <= 2:
            continue
        if c.get('visual_quality', 3) < 3:
            continue
        desc = c.get('desc_clean', c.get('desc', ''))
        if any(k in desc for k in ['似乎', '可能', '大概', '仿佛', '看起来像', '似在']):
            continue

        # 人脸要求：降级模式下跳过
        if strict:
            if mol_type != 'cinematic_beauty' and c.get('face_quality', '') in ['模糊', '无人']:
                continue
            if mol_type != 'cinematic_beauty' and not c.get('face_quality') and c.get('faces', 0) < 1:
                continue
        if mol_type == 'cinematic_beauty' and c.get('face_quality', '') == '模糊':
            continue

        # 事件过滤：降级模式下改为加分而非硬过滤
        target_events = mdef.get('events')
        if target_events and strict:
            event_text = clip_event_text(c)
            if not any(e in event_text for e in target_events):
                continue

        # 景别过滤 (光影美学)：降级模式下跳过
        target_shots = mdef.get('shots')
        if target_shots and strict:
            shot = c.get('_v2', {}).get('shot', '') or ''
            scene_types = c.get('scene_types', [])
            if not any(s in shot or s in str(scene_types) for s in target_shots):
                continue

        pool.append(c)

    return pool


def molecular_filter(clips, mol_type):
    """按分子类型筛选片段池。
    对 legacy 数据自动降级：先尝试严格过滤，不足则放宽阈值。"""
    mdef = MOLECULE_TYPES[mol_type]
    min_needed = mdef.get('min_clips', 3)
    legacy = _is_legacy_data(clips)

    # 先尝试严格过滤
    pool = _molecular_filter_pass(clips, mol_type, strict=True)

    # 如果严格过滤结果不足，且数据为 legacy，降级重试
    if len(pool) < min_needed and legacy:
        pool_relaxed = _molecular_filter_pass(clips, mol_type, strict=False)
        if len(pool_relaxed) > len(pool):
            print(f"    [降级] legacy数据严格过滤仅{len(pool)}条，放宽后{len(pool_relaxed)}条")
            pool = pool_relaxed

    # 最终兜底：如果仍然不足，取 top-N by score（不做硬过滤）
    if len(pool) < min_needed and legacy:
        usable = [c for c in clips if c.get('usable', True) and c.get('reject_reason', '无') == '无']
        if len(usable) > len(pool):
            scored = sorted(usable, key=lambda c: -molecular_score(c, mol_type))
            pool = scored[:max(min_needed * 3, 20)]
            print(f"    [兜底] 按评分取 top-{len(pool)} 候选")

    return pool


def deduplicate_clips(clips, window_seconds=5):
    """通用片段去重：同集且时间窗口内的片段只保留第一个。
    window_seconds: 时间窗口大小（秒），同集内时间差小于此值视为重复。
    返回去重后的列表（保持原始顺序）。"""
    kept = []
    used_windows = set()
    for c in clips:
        ep = int(c.get('ep', '0') or 0)
        t = c.get('time', 0)
        key = (ep, round(t / window_seconds))
        if key in used_windows:
            continue
        used_windows.add(key)
        kept.append(c)
    return kept


def molecular_rank(pool, mol_type, top_n=10):
    """分子类型特定的排序 + 叙事多样性"""
    mdef = MOLECULE_TYPES[mol_type]

    ranked = sorted(pool, key=lambda c: -molecular_score(c, mol_type))

    # 简单叙事多样性: 按 narrative_beats 比例取
    beat_ratio = mdef.get('narrative_beats', {})
    n = min(top_n, len(ranked))
    target_beats = {}
    for beat, ratio in beat_ratio.items():
        target_beats[beat] = max(1, round(n * ratio))

    # 打标签
    for c in ranked:
        c['_nf'] = tag_narrative_function(c)

    # 按节拍选取
    by_beat = {}
    for c in ranked:
        nf = c.get('_nf', 'rising')
        by_beat.setdefault(nf, []).append(c)

    selected = []
    beat_used = {}
    for beat in ['hook', 'setup', 'inciting', 'rising', 'climax', 'transition']:
        need = target_beats.get(beat, 0)
        candidates = by_beat.get(beat, [])
        for c in candidates:
            if need <= 0:
                break
            if c not in selected:
                selected.append(c)
                beat_used[beat] = beat_used.get(beat, 0) + 1
                need -= 1

    # 填满
    if len(selected) < n:
        for c in ranked:
            if c not in selected:
                selected.append(c)
            if len(selected) >= n:
                break

    if mol_type in ['hook_clash', 'quote_rhythm', 'suspense_hook']:
        hook = next((c for c in ranked if is_high_conflict_clip(c)), None)
        if hook:
            selected = [hook] + [c for c in selected if c is not hook]

    spread = deduplicate_clips(selected, window_seconds=5)
    if len(spread) < n:
        # 补充被去重掉的候选
        remaining = [c for c in selected if c not in spread]
        remaining_dedup = deduplicate_clips(remaining, window_seconds=3)
        for c in remaining_dedup:
            if c not in spread:
                spread.append(c)
            if len(spread) >= n:
                break

    return spread[:n]


def cut_molecular_clips(selected, mol_type):
    """按分子类型天花板 + V3 决策字段统一切割。
    V3 提供 per-frame 的 pre_roll / suggested_duration（相对偏移策略），
    分子类型提供 clip_dur 上下限（硬性节奏约束）。"""
    mdef = MOLECULE_TYPES[mol_type]
    clip_dur_range = mdef.get('clip_dur', (3, 5))
    min_dur = clip_dur_range[0]
    max_dur = clip_dur_range[1]

    clip_specs = []
    for i, c in enumerate(selected):
        ep = str(c.get('ep', '1'))
        t = c.get('time', 0)

        # V3 统一公式: start = ts - pre_roll, 所有 cut_role 共用
        pre_roll = float(c.get('pre_roll', 1.0) or 1.0)
        suggested_dur = float(c.get('suggested_duration', 3.0) or 3.0)
        start = max(0, t - pre_roll)
        dur = max(min_dur, min(suggested_dur, max_dur))

        # 金句卡点特殊处理: 上限压到 2.5s 保证 BGM 卡拍
        if mol_type == 'quote_rhythm':
            dur = min(dur, 2.5)

        clip_specs.append({
            "id": f"{mol_type}_{i+1:02d}_EP{int(ep):02d}",
            "ep": ep,
            "start": round(start, 1),
            "dur": round(dur, 1),
        })

    grade = mdef.get('grade',
        "eq=contrast=1.25:saturation=1.1:gamma=1.05,colorbalance=rs=0.02:gs=-0.02:bs=-0.05")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = parallel_cut_clips(clip_specs, SOURCE_DIR, OUTPUT_DIR,
                                 grade_filter=grade, workers=3,
                                 name_func=name_func)
    ok = sum(1 for r in results.values() if r.get("ok"))
    return clip_specs, results, ok


def assemble_molecular(clip_specs, results, mol_type):
    """三段式组装 + BGM"""
    mdef = MOLECULE_TYPES[mol_type]

    clip_files = [os.path.join(OUTPUT_DIR, f"{s['id']}.mp4") for s in clip_specs
                  if results.get(s['id'], {}).get('ok')]

    # 过滤掉过小的损坏文件（< 1KB）
    clip_files = [f for f in clip_files if os.path.exists(f) and os.path.getsize(f) > 1024]

    if len(clip_files) < mdef.get('min_clips', 2):
        print(f"  [ERR] 有效片段不足 {len(clip_files)}")
        return None

    # 生成黑屏(3秒结尾)
    black_file = os.path.join(OUTPUT_DIR, f"_black_{mol_type}.mp4")
    subprocess.run([
        FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=3",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", black_file
    ], capture_output=True)

    # 音频润滑: 每个片段加 afade 淡入淡出，避免拼接爆音
    fade_dur = 0.2
    faded_files = []
    for cf, spec in zip(clip_files, clip_specs):
        faded = os.path.join(OUTPUT_DIR, f"_faded_{spec['id']}.mp4")
        d = spec['dur']
        fade_out_start = max(0.1, d - fade_dur)
        subprocess.run([
            FFMPEG, "-y", "-i", cf,
            "-af", f"afade=t=in:d={fade_dur}:curve=tri,afade=t=out:st={fade_out_start}:d={fade_dur}:curve=tri",
            "-c:v", "copy", faded
        ], capture_output=True, encoding='utf-8', errors='replace')
        if os.path.exists(faded) and os.path.getsize(faded) > 1024:
            faded_files.append(faded)

    concat_out = os.path.join(OUTPUT_DIR, f"_concat_{mol_type}.mp4")
    all_files = [f for f in faded_files if os.path.exists(f)] + [black_file]
    # 黑屏不需要 afade
    all_files = [f for f in all_files if os.path.exists(f)]
    in_args = []
    for cf in all_files:
        in_args.extend(["-i", cf])
    n = len(all_files)
    if n < 2:
        print(f"  [ERR] Concat输入不足: {n}个文件")
        return None
    concat_v = "".join([f"[{i}:v]" for i in range(n)]) + f"concat=n={n}:v=1:a=0[vo]"
    concat_a = "".join([f"[{i}:a]" for i in range(n)]) + f"concat=n={n}:v=0:a=1[ao]"

    subprocess.run([FFMPEG, "-y"] + in_args + [
        "-filter_complex", f"{concat_v};{concat_a}",
        "-map", "[vo]", "-map", "[ao]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-c:a", "aac", "-b:a", "192k", concat_out
    ], capture_output=True, encoding='utf-8', errors='replace')

    # 清理 afade 临时文件
    for f in faded_files:
        if os.path.exists(f): os.remove(f)

    if not os.path.exists(concat_out) or os.path.getsize(concat_out) < 1000:
        print(f"  [ERR] Concat失败")
        return None

    # BGM混合
    output_path = molecule_output_path(mol_type)
    total_dur = sum(s.get('dur', 5) for s in clip_specs) + 3
    bgm_vol = mdef.get('bgm_vol', 0.25)

    if os.path.exists(BGM_FILE):
        cmd = [
            FFMPEG, "-y", "-i", concat_out, "-i", BGM_FILE,
            "-filter_complex",
            f"[1:a]atrim=0:{total_dur+3},volume={bgm_vol},afade=t=out:st={total_dur-3}:d=3[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path
        ]
        subprocess.run(cmd, capture_output=True)
    else:
        subprocess.run([FFMPEG, "-y", "-i", concat_out, "-c", "copy", output_path],
                       capture_output=True)

    # Cleanup
    for cf in clip_files:
        if os.path.exists(cf): os.remove(cf)
    if os.path.exists(concat_out): os.remove(concat_out)
    if os.path.exists(black_file): os.remove(black_file)

    return output_path


def build_molecule_timeline(mol_type, selected, clip_specs, final_path=None, duration=None):
    """导出可供 ffmpeg / Remotion 复用的结构化时间轴。"""
    mdef = MOLECULE_TYPES[mol_type]
    transition_dur = float(_render_cfg.get("transition_duration", 0.3) or 0.3)
    fps = int(_render_cfg.get("fps", 30) or 30)
    resolution = _render_cfg.get("resolution", "1080x1920")
    subtitle_style_map = {
        "hook_clash": "bold_impact",
        "identity_twist": "clean",
        "emotional_resonance": "elegant",
        "quote_rhythm": "bold_impact",
        "cinematic_beauty": "minimal",
        "suspense_hook": "clean",
    }
    overlay_profile_map = {
        "hook_clash": "impact_hook",
        "identity_twist": "identity_reveal",
        "emotional_resonance": "emotional_breath",
        "quote_rhythm": "karaoke_punch",
        "cinematic_beauty": "minimal_frame",
        "suspense_hook": "suspense_break",
    }
    transition_profile_map = {
        "hook_clash": "fast_xfade",
        "identity_twist": "rise_then_burst",
        "emotional_resonance": "slow_fade",
        "quote_rhythm": "beat_cut",
        "cinematic_beauty": "soft_fade",
        "suspense_hook": "hard_cut_to_black",
    }

    clips_out = []
    subtitles = []
    transitions = []
    cursor = 0.0
    for idx, (c, spec) in enumerate(zip(selected, clip_specs)):
        source_time = float(c.get('time', 0) or 0)
        clip_duration = float(spec.get('dur', 0) or 0)
        start_on_timeline = round(cursor, 3)
        end_on_timeline = round(cursor + clip_duration, 3)
        transcript_excerpt = (c.get('transcript_excerpt', '') or c.get('subtitle_text', '') or '').strip()
        clip_entry = {
            "id": spec["id"],
            "source_episode": int(c.get('ep', '0') or 0),
            "source_time": source_time,
            "source_file": name_func(c.get('ep', '0') or 0),
            "cut_start": float(spec["start"]),
            "cut_duration": clip_duration,
            "timeline_start": start_on_timeline,
            "timeline_end": end_on_timeline,
            "cut_role": c.get('cut_role', c.get('_nf', 'rising')),
            "cut_anchor": c.get('cut_anchor', 'visual'),
            "best_cut": c.get('best_cut', 'before_action'),
            "score": molecular_score(c, mol_type),
            "score_breakdown": {
                "emotion": c.get('emotion', 3),
                "action_level": c.get('action_level', 1),
                "visual_quality": c.get('visual_quality', 3),
                "audio_energy": c.get('audio_energy', 1),
                "speech_density": c.get('speech_density', 1),
                "hook_value": c.get('hook_value'),
                "promo_value": c.get('promo_value'),
            },
            "tags": {
                "event": c.get('_v2', {}).get('event', c.get('event_subtype', '')),
                "event_subtype": c.get('event_subtype', ''),
                "face_quality": c.get('face_quality', ''),
                "dialogue_anchor": c.get('dialogue_anchor', 'none'),
                "has_speech_peak": bool(c.get('has_speech_peak', False)),
                "beat_nearby": bool(c.get('beat_nearby', False)),
            },
        }
        clips_out.append(clip_entry)

        if transcript_excerpt:
            subtitles.append({
                "clip_id": spec["id"],
                "text": transcript_excerpt,
                "start": start_on_timeline,
                "end": end_on_timeline,
                "style": subtitle_style_map.get(mol_type, "clean"),
            })

        if idx < len(clip_specs) - 1:
            transitions.append({
                "from": spec["id"],
                "to": clip_specs[idx + 1]["id"],
                "type": "xfade",
                "duration": transition_dur,
                "profile": transition_profile_map.get(mol_type, "fast_xfade"),
            })
            cursor += max(0.0, clip_duration - transition_dur)
        else:
            cursor += clip_duration

    dialogue_windows = []
    for sub in subtitles:
        dialogue_windows.append((sub["start"], sub["end"]))

    qa_meta = {
        "first_clip_high_conflict": is_high_conflict_clip(selected[0]) if selected else False,
        "subtitle_count": len(subtitles),
        "transition_count": len(transitions),
        "empty_subtitles": sum(1 for s in subtitles if not s.get("text")),
    }

    return {
        "schema": "promo_timeline_v1",
        "project_name": PROJECT_NAME,
        "molecule_type": mol_type,
        "molecule_name": mdef["name"],
        "duration": round(duration or cursor, 3),
        "fps": fps,
        "resolution": resolution,
        "render_backend": _render_cfg.get("backend", "ffmpeg"),
        "style_profile": mol_type,
        "overlay_profile": overlay_profile_map.get(mol_type, "clean"),
        "transition_profile": transition_profile_map.get(mol_type, "fast_xfade"),
        "audio_tracks": {
            "dialogue": {"source": "source_clips", "windows": dialogue_windows},
            "bgm": {
                "path": BGM_FILE if os.path.exists(BGM_FILE) else "",
                "volume": mdef.get("bgm_vol", 0.25),
                "style": mdef.get("bgm_style", "default"),
            },
        },
        "clips": clips_out,
        "subtitles": subtitles,
        "transitions": transitions,
        "overlays": [
            {
                "type": "black_tail",
                "duration": 3.0,
                "enabled": True,
            }
        ],
        "qa": qa_meta,
        "output": final_path,
    }


def write_timeline_file(mol_type, selected, clip_specs, final_path=None, duration=None):
    timeline = build_molecule_timeline(mol_type, selected, clip_specs, final_path=final_path, duration=duration)
    timeline_path = os.path.join(OUTPUT_DIR, f"timeline_{mol_type}.json")
    write_json(timeline_path, timeline)
    return timeline_path


def molecular_qa_checks(mol_type, selected, clip_specs):
    """按分子类型目标做专属 QA 审核，返回 warnings 列表和 pass/fail 判定。"""
    mdef = MOLECULE_TYPES[mol_type]
    warnings = []
    scores_breakdown = []

    for i, c in enumerate(selected):
        scores_breakdown.append({
            "idx": i,
            "score": molecular_score(c, mol_type),
            "emotion": c.get('emotion', 3),
            "action_level": c.get('action_level', 1),
            "visual_quality": c.get('visual_quality', 3),
            "audio_energy": c.get('audio_energy', 1),
            "speech_density": c.get('speech_density', 1),
            "has_speech_peak": bool(c.get('has_speech_peak', False)),
        })

    # ── 通用 QA ──
    if len(selected) < mdef.get('min_clips', 3):
        warnings.append({"level": "error", "code": "insufficient_clips",
                         "msg": f"片段数{len(selected)}不足最低要求{mdef['min_clips']}"})

    low_q = sum(1 for c in selected if c.get('visual_quality', 3) < 3)
    if low_q > 0:
        warnings.append({"level": "warn", "code": "low_quality_clips",
                         "msg": f"{low_q}个片段画质低于3"})

    # 重复集数过多
    ep_counts = {}
    for c in selected:
        ep = int(c.get('ep', '0') or 0)
        ep_counts[ep] = ep_counts.get(ep, 0) + 1
    max_same_ep = max(ep_counts.values()) if ep_counts else 0
    if max_same_ep > len(selected) * 0.6:
        warnings.append({"level": "warn", "code": "ep_concentration",
                         "msg": f"同一集出现{max_same_ep}次，覆盖面不足"})

    # ── 分子类型专属 QA ──
    if mol_type == 'hook_clash':
        # 首段必须是高冲突
        if selected and not is_high_conflict_clip(selected[0]):
            warnings.append({"level": "warn", "code": "weak_hook",
                             "msg": "首段不是高冲突片段，钩子力度不足"})
        # 整体冲突密度
        high_conflict_count = sum(1 for c in selected if is_high_conflict_clip(c))
        if high_conflict_count < 2:
            warnings.append({"level": "warn", "code": "low_conflict_density",
                             "msg": f"高冲突片段仅{high_conflict_count}个，冲突密度不足"})
        # 动作强度
        avg_action = sum(c.get('action_level', 1) for c in selected) / max(len(selected), 1)
        if avg_action < 3:
            warnings.append({"level": "info", "code": "low_action_avg",
                             "msg": f"平均动作强度{avg_action:.1f}，建议≥3"})

    elif mol_type == 'quote_rhythm':
        # 对白支撑度
        has_transcript = sum(1 for c in selected if (c.get('transcript_excerpt', '') or '').strip())
        if has_transcript == 0:
            warnings.append({"level": "warn", "code": "no_transcript",
                             "msg": "无任何转写摘录，金句效果无法保证"})
        # 对白密度
        avg_speech = sum(c.get('speech_density', 1) for c in selected) / max(len(selected), 1)
        if avg_speech < 2:
            warnings.append({"level": "info", "code": "low_speech_density",
                             "msg": f"平均对白密度{avg_speech:.1f}，金句类建议≥2"})
        # 节奏点
        has_peak = sum(1 for c in selected if c.get('has_speech_peak') or c.get('beat_nearby'))
        if has_peak == 0:
            warnings.append({"level": "info", "code": "no_rhythm_anchor",
                             "msg": "无语音峰值或节拍点，卡点效果可能不足"})

    elif mol_type == 'emotional_resonance':
        # 情绪建立：至少有递进
        emotions = [c.get('emotion', 3) for c in selected]
        if len(emotions) >= 3 and max(emotions) - min(emotions) < 1:
            warnings.append({"level": "info", "code": "flat_emotion",
                             "msg": "情绪无递进变化，共鸣效果可能平淡"})
        # 尾段留白
        if selected and selected[-1].get('action_level', 1) >= 4:
            warnings.append({"level": "info", "code": "no_tail_breath",
                             "msg": "尾段动作强度高，缺少情绪留白"})
        # 音频能量
        avg_audio = sum(c.get('audio_energy', 1) for c in selected) / max(len(selected), 1)
        if avg_audio < 2:
            warnings.append({"level": "info", "code": "low_audio_energy",
                             "msg": f"平均音频能量{avg_audio:.1f}，情绪类建议≥2"})

    elif mol_type == 'identity_twist':
        # 对白支撑度
        has_dialogue = sum(1 for c in selected if c.get('dialogue_visible') or c.get('speech_density', 1) >= 3)
        if has_dialogue < 2:
            warnings.append({"level": "warn", "code": "weak_dialogue_support",
                             "msg": f"仅{has_dialogue}个片段有对白支撑，身份揭示不够明确"})
        # 信息揭示：需要有对峙/跪地等事件
        event_text_all = ' '.join(clip_event_text(c) for c in selected)
        if not any(k in event_text_all for k in ['对峙', '跪地', '冲突', '反转']):
            warnings.append({"level": "info", "code": "no_twist_event",
                             "msg": "缺少对峙/跪地/反转事件，身份反转感不强"})

    elif mol_type == 'suspense_hook':
        # 结尾断点：最后一个片段应该是高张力
        if selected:
            last = selected[-1]
            if last.get('emotion', 3) < 3 and last.get('action_level', 1) < 3:
                warnings.append({"level": "warn", "code": "weak_cliffhanger",
                                 "msg": "结尾片段张力不足，悬崖断点效果弱"})
        # 整体紧张度
        avg_emo = sum(c.get('emotion', 3) for c in selected) / max(len(selected), 1)
        if avg_emo < 3:
            warnings.append({"level": "info", "code": "low_tension",
                             "msg": f"平均情绪{avg_emo:.1f}，悬念类建议≥3"})

    elif mol_type == 'cinematic_beauty':
        # 画面质量
        avg_vq = sum(c.get('visual_quality', 3) for c in selected) / max(len(selected), 1)
        if avg_vq < 3.5:
            warnings.append({"level": "warn", "code": "low_visual_avg",
                             "msg": f"平均画质{avg_vq:.1f}，光影美学类建议≥3.5"})
        # 景别多样性
        shots = [c.get('_v2', {}).get('shot', '') for c in selected]
        unique_shots = len(set(s for s in shots if s))
        if unique_shots < 2:
            warnings.append({"level": "info", "code": "monotone_shots",
                             "msg": "景别单一，建议混合全景/特写/中景"})

    # 汇总
    error_count = sum(1 for w in warnings if w["level"] == "error")
    warn_count = sum(1 for w in warnings if w["level"] == "warn")
    passed = error_count == 0

    return {
        "passed": passed,
        "error_count": error_count,
        "warn_count": warn_count,
        "warnings": warnings,
        "scores_breakdown": scores_breakdown,
    }


def write_qa_report(mol_type, selected, clip_specs, final_path=None, duration=None, timeline_path=None):
    mdef = MOLECULE_TYPES[mol_type]
    ep_counts = {}
    low_quality = 0
    unusable = 0
    repeated_windows = 0
    seen = set()
    clips_report = []
    for c, spec in zip(selected, clip_specs):
        ep = int(c.get('ep', '0') or 0)
        ep_counts[ep] = ep_counts.get(ep, 0) + 1
        key = (ep, round(c.get('time', 0) / 5))
        if key in seen:
            repeated_windows += 1
        seen.add(key)
        if c.get('visual_quality', 3) < 3:
            low_quality += 1
        if c.get('usable') is False:
            unusable += 1
        clips_report.append({
            "id": spec["id"],
            "ep": ep,
            "source_time": c.get('time', 0),
            "start": spec["start"],
            "dur": spec["dur"],
            "event": c.get('_v2', {}).get('event', ''),
            "event_subtype": c.get('event_subtype', ''),
            "emotion": c.get('emotion', 3),
            "action_level": c.get('action_level', 1),
            "visual_quality": c.get('visual_quality', 3),
            "face_quality": c.get('face_quality', ''),
            "usable": c.get('usable', True),
            "reject_reason": c.get('reject_reason', '无'),
        })

    # 分子类型专属 QA
    mol_qa = molecular_qa_checks(mol_type, selected, clip_specs)

    report = {
        "type": mol_type,
        "name": mdef["name"],
        "target_duration": mdef["target_dur"],
        "output": final_path,
        "timeline": timeline_path,
        "duration": duration,
        "clip_count": len(clip_specs),
        "ep_coverage": sorted(ep_counts.keys()),
        "same_ep_max_count": max(ep_counts.values()) if ep_counts else 0,
        "first_clip_high_conflict": is_high_conflict_clip(selected[0]) if selected else False,
        "unusable_count": unusable,
        "low_quality_count": low_quality,
        "repeated_time_window_count": repeated_windows,
        "molecular_qa": mol_qa,
        "clips": clips_report,
    }
    qa_path = os.path.join(OUTPUT_DIR, f"qa_{mol_type}.json")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(qa_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 打印 QA 摘要
    if mol_qa["warnings"]:
        print(f"  QA审核: {'PASS' if mol_qa['passed'] else 'FAIL'} "
              f"({mol_qa['error_count']}错误, {mol_qa['warn_count']}警告)")
        for w in mol_qa["warnings"]:
            icon = "[ERR]" if w["level"] == "error" else "[WARN]" if w["level"] == "warn" else "[INFO]"
            print(f"    {icon} [{w['code']}] {w['msg']}")

    return qa_path


def process_molecule(clips, mol_type, dry_run=False, export_csv=False, review_selections=None):
    """处理单条分子宣发。
    review_selections: 可选 dict，key 为 (ep, time)，value 为 keep (bool)。
                       传入时跳过自动选片，直接使用人工审核后的片段列表。"""

    mdef = MOLECULE_TYPES[mol_type]
    print(f"\n{'='*60}")
    print(f"  分子类型: {mdef['name']} ({mol_type})")
    print(f"  目标: {mdef['target_dur']}s | {mdef['min_clips']}-{mdef['max_clips']}片段")
    print(f"  规则: {mdef['hook_rule']}")
    print(f"{'='*60}")

    # ── 人工审核模式：从 CSV 读取已确认片段 ──
    if review_selections is not None:
        kept = [c for c in clips if review_selections.get(
            (int(c.get('ep', '0') or 0), int(c.get('time', 0))), False
        )]
        # 补充 clips 中未出现在 review_selections 但在同分子类型中评分高的片段
        if len(kept) < mdef['max_clips']:
            pool = molecular_filter(clips, mol_type)
            for c in molecular_rank(pool, mol_type, top_n=mdef['max_clips'] * 3):
                key = (int(c.get('ep', '0') or 0), int(c.get('time', 0)))
                if not review_selections.get(key, True):  # 明确被标记为 N 的跳过
                    continue
                if key not in {(int(k.get('ep', '0') or 0), int(k.get('time', 0))) for k in kept}:
                    kept.append(c)
                if len(kept) >= mdef['max_clips']:
                    break
        selected = kept[:mdef['max_clips']]
        print(f"  [人工审核] 确认 {len(selected)} 片段 (从审核表读取)")
    else:
        # 预筛选
        pool = molecular_filter(clips, mol_type)
        print(f"  预筛选: {len(clips)} → {len(pool)} 片段")

        if len(pool) < mdef['min_clips']:
            print(f"  [ERR] 候选片段不足")
            return None

        # 排序 + 叙事多样性
        selected = molecular_rank(pool, mol_type, top_n=mdef['max_clips'])
    print(f"\n  选中 {len(selected)} 片段:")
    for i, c in enumerate(selected):
        ep = c.get('ep', '?')
        t = c.get('time', 0)
        emo = c.get('emotion', 3)
        nf = c.get('_nf', '?')
        hint = c.get('desc_clean', c.get('desc', ''))[:50]
        event = c.get('_v2', {}).get('event', '?')
        print(f"  {i+1}. EP{ep} {t}s emo={emo} event={event} func={nf} "
              f"q={c.get('visual_quality', '?')} face={c.get('face_quality', '?')} "
              f"score={molecular_score(c, mol_type):.0f} \"{hint}\"")

    ep_nums = sorted(set(int(c.get('ep', '0') or 0) for c in selected))
    print(f"  覆盖: EP{min(ep_nums)}-EP{max(ep_nums)} ({len(ep_nums)}集)")

    # ── CSV 导出 ──
    if export_csv:
        csv_path = os.path.join(OUTPUT_DIR, f"review_{mol_type}.csv")
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as cf:
            writer = csv.writer(cf)
            writer.writerow(["keep(Y/N)", "ep", "time(s)", "suggested_dur(s)",
                            "cut_role", "best_cut", "event", "subtype", "emo",
                            "visual_quality", "face_quality", "hook_value", "promo_value",
                            "desc"])
            for c in selected:
                desc = (c.get('desc_clean', c.get('desc', '')) or '')[:80]
                writer.writerow([
                    "Y",
                    int(c.get('ep', '0') or 0),
                    c.get('time', 0),
                    round(float(c.get('suggested_duration', 2.5) or 2.5), 1),
                    c.get('cut_role', '?'),
                    c.get('best_cut', '?'),
                    c.get('_v2', {}).get('event', '?'),
                    c.get('event_subtype', ''),
                    c.get('emotion', 3),
                    c.get('visual_quality', 3),
                    c.get('face_quality', '?'),
                    c.get('hook_value', 1),
                    c.get('promo_value', 1),
                    desc,
                ])
        print(f"  [CSV] 审片表: {csv_path}")

    if dry_run:
        # 生成模拟 clip_specs 用于 timeline 预览
        mdef_dr = MOLECULE_TYPES[mol_type]
        clip_dur_range = mdef_dr.get('clip_dur', (3, 5))
        default_dur = (clip_dur_range[0] + clip_dur_range[1]) / 2
        dry_specs = []
        for i, c in enumerate(selected):
            ep = str(c.get('ep', '1'))
            t = c.get('time', 0)
            start = max(0, t - 1.0)
            dry_specs.append({
                "id": f"{mol_type}_{i+1:02d}_EP{int(ep):02d}",
                "ep": ep,
                "start": round(start, 1),
                "dur": round(default_dur, 1),
            })
        timeline_path = write_timeline_file(mol_type, selected, dry_specs, final_path=None, duration=None)
        print(f"  [Dry Run] 跳过切割")
        print(f"  Timeline: {timeline_path}")
        return selected

    # 切割
    clip_specs, results, ok = cut_molecular_clips(selected, mol_type)
    print(f"  切割: {ok}/{len(results)} 成功")

    if ok < mdef['min_clips']:
        print(f"  [ERR] 有效片段不足")
        return None

    # 组装
    final = assemble_molecular(clip_specs, results, mol_type)
    if final and os.path.exists(final):
        dur = float(subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", final], capture_output=True, text=True
        ).stdout.strip())
        timeline_path = write_timeline_file(mol_type, selected, clip_specs, final, dur)
        qa_path = write_qa_report(mol_type, selected, clip_specs, final, dur, timeline_path=timeline_path)
        print(f"  输出: {final}")
        print(f"  QA: {qa_path}")
        print(f"  Timeline: {timeline_path}")
        print(f"  时长: {dur:.1f}s")
        return final
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='分子级拆解宣发生成器')
    parser.add_argument('--type', type=str, default='', help='指定分子类型 (如 hook_clash)')
    parser.add_argument('--types', type=str, default='', help='指定多个分子类型，逗号分隔')
    parser.add_argument('--dry-run', action='store_true', help='仅展示选片，不切割')
    parser.add_argument('--export-csv', action='store_true', help='dry-run 同时导出 CSV 审片表')
    parser.add_argument('--from-csv', type=str, default='', help='从人工审核后的 CSV 读取选片并生成')
    args = parser.parse_args()

    # ── CSV 人工审核模式 ──
    review_selections = None
    if args.from_csv:
        if not os.path.exists(args.from_csv):
            print(f"[ERR] CSV文件不存在: {args.from_csv}")
            return
        review_selections = {}
        with open(args.from_csv, 'r', encoding='utf-8-sig') as cf:
            reader = csv.DictReader(cf)
            for row in reader:
                keep = str(row.get('keep(Y/N)', 'Y')).strip().upper()
                if keep not in ('Y', 'YES', '1'):
                    continue
                try:
                    ep = int(row.get('ep', '0'))
                    t = int(float(row.get('time(s)', '0')))
                    review_selections[(ep, t)] = True
                except (ValueError, TypeError):
                    continue
        print(f"  从CSV读取 {len(review_selections)} 条确认片段")
        if not review_selections:
            print("[ERR] CSV中无有效Y标记片段")
            return

    t_start = time.time()
    print("=" * 60)
    print(f"  {PROJECT_NAME} 分子级拆解宣发")
    print("=" * 60)

    # 加载数据
    print("\n[加载] 审片数据...")
    clips = load_analysis()
    print(f"  共 {len(clips)} 条审片记录")

    if args.types:
        mol_types = [x.strip() for x in args.types.split(',') if x.strip()]
    elif args.type:
        mol_types = [args.type]
    else:
        mol_types = list(MOLECULE_TYPES.keys())
    invalid = [mt for mt in mol_types if mt not in MOLECULE_TYPES]
    if invalid:
        print(f"  未知分子类型: {', '.join(invalid)}")
        print(f"  可用: {', '.join(MOLECULE_TYPES.keys())}")
        return

    # CSV 审核模式：只支持单类型
    if review_selections is not None and len(mol_types) > 1:
        print("[WARN] CSV审核模式一次只支持单个分子类型，仅处理第一个")
        mol_types = mol_types[:1]

    export_csv = args.export_csv or bool(args.from_csv)  # from_csv 也隐式导出

    results = []
    for mt in mol_types:
        try:
            r = process_molecule(clips, mt, dry_run=args.dry_run,
                                export_csv=export_csv,
                                review_selections=review_selections)
            results.append(r)
        except Exception as e:
            print(f"  [ERR] {mt} 失败: {e}")
            import traceback
            traceback.print_exc()

    if not args.dry_run or args.export_csv:
        succeeded = sum(1 for r in results if r)
        print(f"\n{'='*60}")
        print(f"  完成! {succeeded}/{len(mol_types)} 条生成成功")
        print(f"  耗时: {time.time() - t_start:.0f}s")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
