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
import sys, os, json, time, subprocess, re
from pathlib import Path

# ── Path setup ──
SKILL_DIR = Path(r"E:\技能skills\剪辑skills_backup_2")
sys.path.insert(0, str(SKILL_DIR))

from edit_utils import (parse_vision_line, parallel_cut_clips,
                        check_audio_quality, fact_check_selection)
from genre_engine import (_genre_detect_two_pass, multi_pass_rank,
                          EDIT_DIRECTIONS,
                          recommend_directions, apply_direction_weights,
                          prefilter_for_direction, enforce_narrative_diversity,
                          tag_narrative_function)

# ── Config ──
SOURCE_DIR = r"E:\BaiduNetdiskDownload\一品布衣（105集）潘子健&胡家荣"
ANALYSIS_FILE = r"E:\视频\一品布衣\analysis_v2.txt"  # 优先用V2
ANALYSIS_FALLBACK = r"E:\视频\一品布衣\analysis.txt"
OUTPUT_DIR = r"E:\视频\一品布衣\_work_molecular"
BGM_FILE = r"E:\BaiduNetdiskDownload\05.终宋（76集）陈外＆王涵\clips\ambient.wav"

import json as _json
_cfg = _json.loads((SKILL_DIR / "config.json").read_text('utf-8'))
FFMPEG = _cfg.get("ffmpeg", "ffmpeg")
FFPROBE = _cfg.get("ffprobe", "ffprobe")


def name_func(ep):
    return f"{int(ep)}.mp4"

# ── 分子类型定义 ──
MOLECULE_TYPES = {
    "hook_clash": {
        "name": "冲突钩子",
        "output": r"E:\视频\一品布衣\一品布衣_宣发_冲突钩子.mp4",
        "target_dur": 15,
        "min_clips": 3,
        "max_clips": 5,
        "clip_dur": (2.5, 4.5),
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
        "output": r"E:\视频\一品布衣\一品布衣_宣发_身份反转.mp4",
        "target_dur": 18,
        "min_clips": 4,
        "max_clips": 6,
        "clip_dur": (2.5, 5.0),
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
        "output": r"E:\视频\一品布衣\一品布衣_宣发_情感共鸣.mp4",
        "target_dur": 25,
        "min_clips": 4,
        "max_clips": 7,
        "clip_dur": (3.0, 6.0),
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
        "output": r"E:\视频\一品布衣\一品布衣_宣发_金句卡点.mp4",
        "target_dur": 12,
        "min_clips": 3,
        "max_clips": 5,
        "clip_dur": (1.5, 3.0),
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
        "output": r"E:\视频\一品布衣\一品布衣_宣发_光影美学.mp4",
        "target_dur": 18,
        "min_clips": 4,
        "max_clips": 6,
        "clip_dur": (3.0, 5.0),
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
        "output": r"E:\视频\一品布衣\一品布衣_宣发_悬念钩子.mp4",
        "target_dur": 13,
        "min_clips": 3,
        "max_clips": 4,
        "clip_dur": (2.0, 3.5),
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

    # V1数据仅保留V2未覆盖的集（去重：同ep且时间差<10s视为同一帧）
    v1_added = 0
    for c in v1_clips:
        ep = int(c.get('ep', '0') or 0)
        ts = c.get('time', 0)
        # 检查是否与V2已有帧冲突（同集且时间差<10s）
        is_dup = False
        for v2_ep, v2_ts in v2_keys:
            if v2_ep == ep and abs(v2_ts - ts) < 10:
                is_dup = True
                break
        if not is_dup:
            clips.append(c)
            v1_added += 1

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
    for target in mdef.get('events') or []:
        if target in event_text:
            score += 18
    for subtype in ['受伤', '倒地', '抓扯', '持械', '追逐', '怒吼', '哭泣', '奇幻爆发']:
        if subtype in event_text:
            score += 12
    if mol_type == 'cinematic_beauty':
        score += c.get('visual_quality', 3) * 10
        if c.get('_v2', {}).get('shot') in ['全景', '特写']:
            score += 15
        if c.get('face_quality', '') == '无人':
            score += 4
    if c.get('reject_reason', '无') != '无' or not c.get('usable', True):
        score -= 100
    if c.get('event_conf', '') == '模糊':
        score -= 50
    return score


def molecular_filter(clips, mol_type):
    """按分子类型筛选片段池"""
    mdef = MOLECULE_TYPES[mol_type]
    pool = []

    for c in clips:
        emo = c.get('emotion', 3)
        if emo < mdef.get('emo_min', 3):
            continue
        if c.get('usable') is False:
            continue
        if c.get('reject_reason', '无') != '无':
            continue
        if c.get('event_conf', '') == '模糊':
            continue
        if c.get('visual_quality', 3) < 3:
            continue
        desc = c.get('desc_clean', c.get('desc', ''))
        if any(k in desc for k in ['似乎', '可能', '大概', '仿佛', '看起来像', '似在']):
            continue
        if mol_type != 'cinematic_beauty' and c.get('face_quality', '') in ['模糊', '无人']:
            continue
        if mol_type != 'cinematic_beauty' and not c.get('face_quality') and c.get('faces', 0) < 1:
            continue
        if mol_type == 'cinematic_beauty' and c.get('face_quality', '') == '模糊':
            continue

        # 事件过滤
        target_events = mdef.get('events')
        if target_events:
            event_text = clip_event_text(c)
            if not any(e in event_text for e in target_events):
                continue

        # 景别过滤 (光影美学)
        target_shots = mdef.get('shots')
        if target_shots:
            shot = c.get('_v2', {}).get('shot', '') or ''
            scene_types = c.get('scene_types', [])
            if not any(s in shot or s in str(scene_types) for s in target_shots):
                continue

        pool.append(c)

    return pool


def molecular_rank(pool, mol_type, top_n=8):
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

    spread = []
    used_windows = set()
    for c in selected:
        key = (int(c.get('ep', '0') or 0), round(c.get('time', 0) / 5))
        if key in used_windows:
            continue
        used_windows.add(key)
        spread.append(c)
    if len(spread) < n:
        for c in selected:
            key = (int(c.get('ep', '0') or 0), round(c.get('time', 0) / 5))
            if key in used_windows:
                continue
            if c not in spread:
                used_windows.add(key)
                spread.append(c)
            if len(spread) >= n:
                break

    return spread[:n]


def cut_molecular_clips(selected, mol_type):
    """按分子类型参数切割片段"""
    mdef = MOLECULE_TYPES[mol_type]
    clip_dur_range = mdef.get('clip_dur', (3, 5))
    default_dur = (clip_dur_range[0] + clip_dur_range[1]) / 2

    clip_specs = []
    for i, c in enumerate(selected):
        ep = str(c.get('ep', '1'))
        t = c.get('time', 0)
        if mol_type == 'hook_clash':
            start = max(0, t - 0.5)
            dur = 2.5 if i == 0 else round(default_dur, 1)
        elif mol_type == 'quote_rhythm':
            start = max(0, t - 0.3)
            dur = min(2.2, round(default_dur, 1))
        elif mol_type == 'suspense_hook':
            start = max(0, t - 3.0)
            dur = 3.0 if i == len(selected) - 1 else round(default_dur, 1)
        elif mol_type == 'emotional_resonance':
            start = max(0, t - 1.5)
            dur = min(6.0, max(4.0, round(default_dur, 1)))
        elif mol_type == 'identity_twist':
            start = max(0, t - (2.0 if i < 2 else 0.8))
            dur = 4.5 if i < 2 else 3.0
        else:
            start = max(0, t - 1)
            dur = round(default_dur, 1)
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

    if len(clip_files) < mdef.get('min_clips', 2):
        print(f"  ❌ 有效片段不足 {len(clip_files)}")
        return None

    # 生成黑屏(3秒结尾)
    black_file = os.path.join(OUTPUT_DIR, f"_black_{mol_type}.mp4")
    subprocess.run([
        FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=3",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", black_file
    ], capture_output=True)

    # Concat
    concat_out = os.path.join(OUTPUT_DIR, f"_concat_{mol_type}.mp4")
    in_args = []
    all_files = clip_files + [black_file]
    for cf in all_files:
        if os.path.exists(cf):
            in_args.extend(["-i", cf])
    n = len(all_files)
    concat_v = "".join([f"[{i}:v]" for i in range(n)]) + f"concat=n={n}:v=1:a=0[vo]"
    concat_a = "".join([f"[{i}:a]" for i in range(n)]) + f"concat=n={n}:v=0:a=1[ao]"

    subprocess.run([FFMPEG, "-y"] + in_args + [
        "-filter_complex", f"{concat_v};{concat_a}",
        "-map", "[vo]", "-map", "[ao]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-c:a", "aac", "-b:a", "192k", concat_out
    ], capture_output=True, text=True)

    if not os.path.exists(concat_out) or os.path.getsize(concat_out) < 1000:
        print(f"  ❌ Concat失败")
        return None

    # BGM混合
    output_path = mdef['output']
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


def write_qa_report(mol_type, selected, clip_specs, final_path=None, duration=None):
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

    report = {
        "type": mol_type,
        "name": mdef["name"],
        "target_duration": mdef["target_dur"],
        "output": final_path,
        "duration": duration,
        "clip_count": len(clip_specs),
        "ep_coverage": sorted(ep_counts.keys()),
        "same_ep_max_count": max(ep_counts.values()) if ep_counts else 0,
        "first_clip_high_conflict": is_high_conflict_clip(selected[0]) if selected else False,
        "unusable_count": unusable,
        "low_quality_count": low_quality,
        "repeated_time_window_count": repeated_windows,
        "clips": clips_report,
    }
    qa_path = os.path.join(OUTPUT_DIR, f"qa_{mol_type}.json")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(qa_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return qa_path


def process_molecule(clips, mol_type, dry_run=False):
    """处理单条分子宣发"""
    mdef = MOLECULE_TYPES[mol_type]
    print(f"\n{'='*60}")
    print(f"  分子类型: {mdef['name']} ({mol_type})")
    print(f"  目标: {mdef['target_dur']}s | {mdef['min_clips']}-{mdef['max_clips']}片段")
    print(f"  规则: {mdef['hook_rule']}")
    print(f"{'='*60}")

    # 预筛选
    pool = molecular_filter(clips, mol_type)
    print(f"  预筛选: {len(clips)} → {len(pool)} 片段")

    if len(pool) < mdef['min_clips']:
        print(f"  ❌ 候选片段不足")
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

    if dry_run:
        print(f"  [Dry Run] 跳过切割")
        return selected

    # 切割
    clip_specs, results, ok = cut_molecular_clips(selected, mol_type)
    print(f"  切割: {ok}/{len(results)} 成功")

    if ok < mdef['min_clips']:
        print(f"  ❌ 有效片段不足")
        return None

    # 组装
    final = assemble_molecular(clip_specs, results, mol_type)
    if final and os.path.exists(final):
        dur = float(subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", final], capture_output=True, text=True
        ).stdout.strip())
        qa_path = write_qa_report(mol_type, selected, clip_specs, final, dur)
        print(f"  输出: {final}")
        print(f"  QA: {qa_path}")
        print(f"  时长: {dur:.1f}s")
        return final
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='分子级拆解宣发生成器')
    parser.add_argument('--type', type=str, default='', help='指定分子类型 (如 hook_clash)')
    parser.add_argument('--types', type=str, default='', help='指定多个分子类型，逗号分隔')
    parser.add_argument('--dry-run', action='store_true', help='仅展示选片，不切割')
    args = parser.parse_args()

    t_start = time.time()
    print("=" * 60)
    print("  一品布衣 分子级拆解宣发")
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

    results = []
    for mt in mol_types:
        try:
            r = process_molecule(clips, mt, dry_run=args.dry_run)
            results.append(r)
        except Exception as e:
            print(f"  ❌ {mt} 失败: {e}")

    if not args.dry_run:
        succeeded = sum(1 for r in results if r)
        print(f"\n{'='*60}")
        print(f"  完成! {succeeded}/{len(mol_types)} 条生成成功")
        print(f"  耗时: {time.time() - t_start:.0f}s")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
