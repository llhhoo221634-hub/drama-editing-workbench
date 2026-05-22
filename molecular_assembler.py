"""
molecular_assembler.py — 片段切割、转场组装、时间轴导出与QA审核
"""
import os, subprocess, json

from config import get_engine_config, get_project_config
from edit_utils import (parallel_cut_clips, write_json, episode_filename)
from molecule_types import MOLECULE_TYPES, TRANSITION_PROFILE_MAP, molecular_score, is_high_conflict_clip, clip_event_text

def _check_run(proc, label="ffmpeg", output_path=None):
    """检查 subprocess 运行结果，失败时抛异常。"""
    if proc.returncode != 0:
        err = (proc.stderr or b'') if isinstance(proc.stderr, bytes) else str(proc.stderr or '')
        stderr = err[-200:] if isinstance(err, str) else err.decode('utf-8', errors='replace')[-200:]
        raise RuntimeError(f"{label} failed (rc={proc.returncode}): {stderr}")
    if output_path and (not os.path.exists(output_path) or os.path.getsize(output_path) < 1024):
        raise RuntimeError(f"{label} output invalid/missing: {output_path}")

# ── Config ──
_cfg = get_engine_config()
_project = get_project_config()
_render_cfg = (_cfg.get("render") or {}) if isinstance(_cfg.get("render"), dict) else {}
SOURCE_DIR = _project["media_dir"]
OUTPUT_DIR = _project["molecular_dir"]
BGM_FILE = _project["bgm"]
PROJECT_NAME = _project["project_name"]
EPISODE_NAME_TEMPLATE = _project["episode_name_template"]
FFMPEG = _cfg.get("ffmpeg", "") or "ffmpeg"
FFPROBE = _cfg.get("ffprobe", "") or "ffprobe"

if not FFMPEG or not os.path.exists(FFMPEG):
    from edit_utils import load_engine_config
    _full_cfg = load_engine_config()
    _ff = _full_cfg.get("ffmpeg", "ffmpeg") or "ffmpeg"
    if os.path.exists(_ff):
        FFMPEG = _ff
    _ffp = _full_cfg.get("ffprobe", "ffprobe") or "ffprobe"
    if os.path.exists(_ffp):
        FFPROBE = _ffp


def _name_func(ep):
    return episode_filename(ep, EPISODE_NAME_TEMPLATE)


def _mol_output_path(mol_type):
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
                                 name_func=_name_func)
    ok = sum(1 for r in results.values() if r.get("ok"))
    return clip_specs, results, ok


def assemble_molecular(clip_specs, results, mol_type, xfade_type='fade'):
    """三段式组装 + BGM + xfade转场"""
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
    r = subprocess.run([
        FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=3",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", black_file
    ], capture_output=True)
    _check_run(r, "ffmpeg")

    concat_out = os.path.join(OUTPUT_DIR, f"_concat_{mol_type}.mp4")

    if xfade_type == 'none':
        # ── 原逻辑：afade 音频平滑 + concat 拼接 ──
        fade_dur = 0.2
        faded_files = []
        for cf, spec in zip(clip_files, clip_specs):
            faded = os.path.join(OUTPUT_DIR, f"_faded_{spec['id']}.mp4")
            d = spec['dur']
            fade_out_start = max(0.1, d - fade_dur)
            r = subprocess.run([
                FFMPEG, "-y", "-i", cf,
                "-af", f"afade=t=in:d={fade_dur}:curve=tri,afade=t=out:st={fade_out_start}:d={fade_dur}:curve=tri",
                "-c:v", "copy", faded
            ], capture_output=True, encoding='utf-8', errors='replace')
            _check_run(r, "ffmpeg")
            if os.path.exists(faded) and os.path.getsize(faded) > 1024:
                faded_files.append(faded)

        all_files = [f for f in faded_files if os.path.exists(f)] + [black_file]
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

        r = subprocess.run([FFMPEG, "-y"] + in_args + [
            "-filter_complex", f"{concat_v};{concat_a}",
            "-map", "[vo]", "-map", "[ao]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", concat_out
        ], capture_output=True, encoding='utf-8', errors='replace')
        _check_run(r, "ffmpeg")

        # 清理 afade 临时文件
        for f in faded_files:
            if os.path.exists(f): os.remove(f)
    else:
        # ── xfade 视频转场 + acrossfade 音频过渡 ──
        xfade_dur = 0.3

        # 先对每个片段做轻量 afade 防爆音
        fade_dur = 0.2
        faded_files = []
        for cf, spec in zip(clip_files, clip_specs):
            faded = os.path.join(OUTPUT_DIR, f"_faded_{spec['id']}.mp4")
            d = spec['dur']
            fade_out_start = max(0.1, d - fade_dur)
            r = subprocess.run([
                FFMPEG, "-y", "-i", cf,
                "-af", f"afade=t=in:d={fade_dur}:curve=tri,afade=t=out:st={fade_out_start}:d={fade_dur}:curve=tri",
                "-c:v", "copy", faded
            ], capture_output=True, encoding='utf-8', errors='replace')
            _check_run(r, "ffmpeg")
            if os.path.exists(faded) and os.path.getsize(faded) > 1024:
                faded_files.append(faded)

        all_files = [f for f in faded_files if os.path.exists(f)] + [black_file]
        all_files = [f for f in all_files if os.path.exists(f)]

        n = len(all_files)
        if n < 2:
            print(f"  [ERR] xfade输入不足: {n}个文件")
            return None

        in_args = []
        for cf in all_files:
            in_args.extend(["-i", cf])

        # 片段时长列表（用于计算 xfade offset）
        durations = [s['dur'] for s in clip_specs] + [3.0]

        # Video xfade chain
        video_filters = []
        offset = durations[0] - xfade_dur
        video_filters.append(
            f"[0:v][1:v]xfade=transition={xfade_type}:duration={xfade_dur}:offset={offset}[v0]")
        cum_dur = durations[0] + durations[1] - xfade_dur
        for i in range(2, n):
            offset = cum_dur - xfade_dur
            video_filters.append(
                f"[v{i-2}][{i}:v]xfade=transition={xfade_type}:duration={xfade_dur}:offset={offset}[v{i-1}]")
            cum_dur = cum_dur + durations[i] - xfade_dur

        video_chain = ";".join(video_filters)

        # Audio acrossfade chain
        audio_filters = []
        audio_filters.append(f"[0:a][1:a]acrossfade=d={xfade_dur}:c1=tri:c2=tri[a0]")
        for i in range(2, n):
            audio_filters.append(f"[a{i-2}][{i}:a]acrossfade=d={xfade_dur}:c1=tri:c2=tri[a{i-1}]")

        audio_chain = ";".join(audio_filters)

        filter_complex = f"{video_chain};{audio_chain}"
        last_v = f"[v{n-2}]"
        last_a = f"[a{n-2}]"

        r = subprocess.run([FFMPEG, "-y"] + in_args + [
            "-filter_complex", filter_complex,
            "-map", last_v, "-map", last_a,
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", concat_out
        ], capture_output=True, encoding='utf-8', errors='replace')
        _check_run(r, "ffmpeg")

        # 清理 afade 临时文件
        for f in faded_files:
            if os.path.exists(f): os.remove(f)

    if not os.path.exists(concat_out) or os.path.getsize(concat_out) < 1000:
        print(f"  [ERR] Concat失败")
        return None

    # BGM混合
    output_path = _mol_output_path(mol_type)
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
        r = subprocess.run(cmd, capture_output=True)
        _check_run(r, "ffmpeg")
    else:
        r = subprocess.run([FFMPEG, "-y", "-i", concat_out, "-c", "copy", output_path], capture_output=True)
        _check_run(r, "ffmpeg")

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
            "source_file": _name_func(c.get('ep', '0') or 0),
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
                "profile": TRANSITION_PROFILE_MAP.get(mol_type, "fast_xfade"),
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
        "transition_profile": TRANSITION_PROFILE_MAP.get(mol_type, "fast_xfade"),
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
