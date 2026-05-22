"""
build_analysis_v3.py — enrich analysis_v2.txt into edit-ready V3 labels.

Reads existing V2/V1-compatible lines, adds offline editing-decision fields, and writes:
  - analysis_v3.txt
  - analysis_v3_rejects.txt
"""
import json
import os
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

from edit_utils import parse_vision_line, load_project_config

_project = load_project_config()
ANALYSIS_V2 = _project["analysis_v2"]
ANALYSIS_V3 = _project["analysis_v3"]
ANALYSIS_FALLBACK = _project.get("analysis_fallback", "")
REJECTS_OUT = os.path.join(_project["work_dir"], "analysis_v3_rejects.txt")

BAD_HINT_WORDS = ["似乎", "可能", "大概", "仿佛", "看起来像", "似在", "意味着"]
HIGH_CONFLICT = ["冲突", "打斗", "威胁", "对峙", "怒吼", "抓扯", "持械", "追逐", "奇幻爆发", "围攻"]
EMOTIONAL = ["悲伤", "哭泣", "痛苦", "受伤", "流泪", "崩溃"]
VISUAL = ["全景", "特写", "发光", "光束", "夜间", "火把", "血迹"]


def clamp(value, low=1, high=5):
    return max(low, min(high, int(value)))


def parse_line(line):
    parts = line.strip().split(" ", 2)
    if len(parts) < 3:
        return None
    frame_id, time_text, raw = parts
    parsed = parse_vision_line(raw)
    try:
        data = json.loads(raw)
    except Exception:
        data = dict(parsed.get("_v2", {}))
    if not data:
        data = {"hint": parsed.get("desc_clean", ""), "emo": parsed.get("emotion", 3), "event": "其他"}
    ep_match = re.match(r"ep(\d+)_f", frame_id)
    data["ep"] = int(ep_match.group(1)) if ep_match else int(parsed.get("ep") or 0)
    data["frame_id"] = frame_id
    data["timestamp"] = float(time_text.replace("s", "")) if time_text.endswith("s") else parsed.get("timestamp", 0)
    return frame_id, data, parsed


def text_blob(data, parsed):
    values = [
        data.get("event", ""), data.get("event_subtype", ""), data.get("hint", ""),
        data.get("mood", ""), data.get("props", ""), data.get("shot", ""),
        data.get("scene", ""), data.get("subtitle_text", ""), parsed.get("desc_clean", ""),
        " ".join(parsed.get("scene_types", [])),
    ]
    chars = data.get("chars", "")
    if isinstance(chars, list):
        values.append(json.dumps(chars, ensure_ascii=False))
    else:
        values.append(str(chars))
    return " ".join(str(v) for v in values if v)


def infer_reject(data, parsed, blob):
    reason = data.get("reject_reason", "无") or "无"
    visual_quality = clamp(data.get("visual_quality", 3))
    shot = data.get("shot", "") or ""
    subtitle_text = (data.get("subtitle_text", "") or "").strip()
    subtitle_len = len(subtitle_text)
    if reason != "无":
        if reason == "低质量" and visual_quality >= 3:
            reason = "无"
        elif reason == "纯文字" and visual_quality >= 4 and subtitle_len < 12 and shot != "空镜":
            reason = "无"
        else:
            return reason
    if any(k in blob for k in ["许可证", "发行许可证", "网络剧片"]):
        return "片头"
    if any(k in blob for k in ["剧终", "完结", "片尾"]):
        return "片尾"
    if any(k in blob for k in ["纯白", "白屏", "大面积过曝"]):
        return "白屏"
    if any(k in blob for k in ["纯黑", "黑屏"]):
        return "黑屏"
    if shot == "空镜" and visual_quality <= 2:
        return "空镜"
    if subtitle_len >= 12 and any(k in blob for k in ["纯文字", "文字卡", "竖排文字"]):
        return "纯文字"
    if data.get("event_conf") == "模糊" and visual_quality <= 2:
        return "模糊"
    if any(k in data.get("hint", "") for k in BAD_HINT_WORDS):
        return "推测污染"
    return "无"


def infer_event_subtype(data, blob, reject_reason):
    subtype = data.get("event_subtype", "") or "无"
    if subtype != "无":
        return subtype
    if reject_reason in ["片头", "片尾", "纯文字", "白屏", "黑屏", "空镜"]:
        return reject_reason
    rules = [
        ("受伤", ["血", "伤", "痛苦", "伤口"]),
        ("倒地", ["倒地", "躺在", "仰面", "摔倒"]),
        ("抓扯", ["抓", "扯", "按住", "拉住"]),
        ("持械", ["持刀", "刀剑", "武器", "长条状物", "石锤"]),
        ("怒吼", ["怒吼", "张口怒", "呐喊"]),
        ("哭泣", ["泪", "流泪", "眼含泪"]),
        ("奇幻爆发", ["发光", "能量", "光束", "悬浮"]),
        ("被围堵", ["围观", "围住", "多人", "一群"]),
    ]
    for name, keys in rules:
        if any(k in blob for k in keys):
            return name
    return subtype


def infer_conflict_side(blob):
    if any(k in blob for k in ["一群", "多人", "围住", "围观"]):
        return "群体冲突"
    if any(k in blob for k in ["两名", "另一名", "对峙", "对打"]):
        return "双人对峙"
    if any(k in blob for k in ["怒吼", "悬浮", "张口"]):
        return "单人爆发"
    if any(k in blob for k in ["泪", "痛苦", "悲伤"]):
        return "情绪崩溃"
    return "无"


def infer_cut_role(data, blob, promo_value, hook_value):
    event = data.get("event", "")
    subtype = data.get("event_subtype", "")
    if hook_value >= 4:
        return "hook"
    if event in ["打斗"] or subtype in ["奇幻爆发", "怒吼", "倒地"]:
        return "climax"
    if event in ["冲突", "威胁", "对峙"]:
        return "rise"
    if event in ["跪地", "悲伤"] or subtype in ["哭泣", "受伤"]:
        return "setup"
    if promo_value <= 2:
        return "ending"
    return "rise"


def enrich(data, parsed):
    blob = text_blob(data, parsed)
    reject_reason = infer_reject(data, parsed, blob)
    usable = reject_reason == "无"
    visual_quality = clamp(data.get("visual_quality", 3))
    action_level = clamp(data.get("action_level", 1))
    emotion = clamp(data.get("emo", parsed.get("emotion", 3)))
    speech_density = clamp(data.get("speech_density", 1))
    audio_energy = clamp(data.get("audio_energy", 1))
    has_speech_peak = bool(data.get("has_speech_peak", False))
    beat_nearby = bool(data.get("beat_nearby", False))
    transcript_excerpt = (data.get("transcript_excerpt", "") or "").strip()
    dialogue_anchor = data.get("dialogue_anchor", "none") or "none"
    data["event_subtype"] = infer_event_subtype(data, blob, reject_reason)
    data["reject_reason"] = reject_reason
    data["usable"] = usable
    data["event_conf"] = "模糊" if reject_reason != "无" else data.get("event_conf", "可见")

    # ── 趋势标签驱动评分（替代旧关键词加权）──
    action_dir = data.get("action_direction", "静止")
    emotion_trend = data.get("emotion_trend", "稳定")

    # 趋势组合 → 基础分映射
    trend_score_map = {
        ('爆发', '增强'): (5, 5),   # (promo_base, hook_base)
        ('爆发', '持续'): (4, 4),
        ('爆发', '静止'): (3, 3),
        ('上升', '增强'): (4, 4),
        ('上升', '持续'): (3, 3),
        ('上升', '静止'): (2, 2),
        ('稳定', '增强'): (3, 3),
        ('稳定', '持续'): (2, 2),
        ('稳定', '静止'): (1, 1),
        ('下降', '增强'): (2, 2),
        ('下降', '持续'): (2, 1),
        ('下降', '静止'): (1, 1),
    }
    trend_promo, trend_hook = trend_score_map.get(
        (emotion_trend, action_dir), (2, 2))

    # 叠加规则修正: 关键词 + 人脸 + 字幕 + 音频 + event_subtype 动作微调
    if any(k in blob for k in HIGH_CONFLICT):
        trend_hook += 1
        trend_promo += 1
    # event_subtype 高能动作词库：增强型动作额外加分（解决 action 缺 3/5 问题）
    HIGH_ACTION_SUBTYPES = ["抓扯", "持械", "怒吼", "掐", "摔", "血", "刀", "剑", "拳"]
    if action_dir == "增强" and any(k in blob for k in HIGH_ACTION_SUBTYPES):
        trend_hook += 1
        data["action_level"] = min(5, data.get("action_level", 1) + 1)
    if data.get("face_quality", "") in ["正脸", "半面"]:
        trend_hook += 1
        trend_promo += 1
    subtitle = (data.get("subtitle_text", "") or "").strip()
    if len(subtitle) >= 4:
        trend_hook += 1
    if has_speech_peak or beat_nearby:
        trend_hook += 1
    if not usable:
        trend_hook -= 4
        trend_promo -= 4

    promo_value = clamp(trend_promo)
    hook_value = clamp(trend_hook)

    # ── aesthetic_score: 独立加权公式 ──
    composition_type = parsed.get("composition_type", "无") or "无"
    color_palette = parsed.get("color_palette", "自然") or "自然"
    light_beauty = parsed.get("light_beauty", "普通") or "普通"

    composition_score = {"对称": 3, "引导线": 3, "三分法": 2, "中心对称": 1}.get(composition_type, 0)
    light_score = {"电影级": 5, "良好": 3, "普通": 1, "差": 0}.get(light_beauty, 1)
    color_score = {"暖": 2, "冷": 2, "高饱和": 2, "低饱和": 1, "自然": 1}.get(color_palette, 1)

    aesthetic_score = composition_score + light_score + color_score
    cinematic_beauty = clamp(aesthetic_score, 1, 5)

    # 保留旧字段的计算（兼容下游）
    emotion_value = clamp(emotion)
    action_value = clamp(action_level)
    visual_value = clamp(visual_quality)

    dialogue_value = speech_density
    if transcript_excerpt:
        dialogue_value += 1
    if dialogue_anchor in ["boundary", "dense_speech"]:
        dialogue_value += 1
    dialogue_value = clamp(dialogue_value)

    rhythm_value = audio_energy
    if beat_nearby:
        rhythm_value += 1
    if has_speech_peak:
        rhythm_value += 1
    rhythm_value = clamp(rhythm_value)

    audio_hook_value = audio_energy
    if has_speech_peak:
        audio_hook_value += 2
    if beat_nearby:
        audio_hook_value += 1
    audio_hook_value = clamp(audio_hook_value)

    if transcript_excerpt and dialogue_anchor in ["boundary", "dense_speech"]:
        cut_anchor = "dialogue"
    elif has_speech_peak or beat_nearby:
        cut_anchor = "impact_audio"
    else:
        cut_anchor = "visual"

    cut_role = infer_cut_role(data, blob, promo_value, hook_value)
    if cut_role == "hook":
        best_cut, pre_roll, post_roll, duration = "on_action", 0.5, 2.0, 2.5
    elif cut_role == "climax":
        best_cut, pre_roll, post_roll, duration = "before_action", 1.0, 2.5, 3.5
    elif cut_role == "setup":
        best_cut, pre_roll, post_roll, duration = "after_reaction", 1.5, 3.5, 5.0
    elif cut_role == "ending":
        best_cut, pre_roll, post_roll, duration = "after_reaction", 0.5, 1.5, 2.0
    else:
        best_cut, pre_roll, post_roll, duration = "before_action", 1.0, 2.5, 3.5

    data.update({
        "v3_schema": "analysis_v3_edit_decision_v1",
        "quality_score": visual_quality,
        "promo_value": promo_value,
        "hook_value": hook_value,
        "emotion_value": emotion_value,
        "action_value": action_value,
        "visual_value": visual_value,
        "dialogue_value": dialogue_value,
        "rhythm_value": rhythm_value,
        "audio_hook_value": audio_hook_value,
        "cinematic_beauty": cinematic_beauty,
        "aesthetic_score": aesthetic_score,
        "composition_type": composition_type,
        "color_palette": color_palette,
        "light_beauty": light_beauty,
        "cut_anchor": cut_anchor,
        "conflict_side": infer_conflict_side(blob),
        "cut_role": cut_role,
        "best_cut": best_cut,
        "pre_roll": pre_roll,
        "post_roll": post_roll,
        "suggested_duration": duration,
        "source_format": parsed.get("format", "legacy"),
    })
    return data


def apply_percentile_braking(enriched_frames):
    """两阶段分位数熔断：全局排名后映射 hook_value 和 promo_value 到 1-5。
    top-5% → 5, top-5-15% → 4, top-15-35% → 3, top-35-60% → 2, bottom-40% → 1"""
    if not enriched_frames:
        return enriched_frames

    # 按原始 hook_value 降序排列
    sorted_hook = sorted(enriched_frames, key=lambda d: -d.get("hook_value", 1))
    sorted_promo = sorted(enriched_frames, key=lambda d: -d.get("promo_value", 1))
    n = len(enriched_frames)

    # 分位点
    p5 = max(1, int(n * 0.05))
    p15 = max(1, int(n * 0.15))
    p35 = max(1, int(n * 0.35))
    p60 = max(1, int(n * 0.60))

    # hook_value 分位映射
    for rank, frame in enumerate(sorted_hook):
        if rank < p5:
            frame["hook_value"] = 5
        elif rank < p15:
            frame["hook_value"] = 4
        elif rank < p35:
            frame["hook_value"] = 3
        elif rank < p60:
            frame["hook_value"] = 2
        else:
            frame["hook_value"] = 1

    # promo_value 分位映射
    for rank, frame in enumerate(sorted_promo):
        if rank < p5:
            frame["promo_value"] = 5
        elif rank < p15:
            frame["promo_value"] = 4
        elif rank < p35:
            frame["promo_value"] = 3
        elif rank < p60:
            frame["promo_value"] = 2
        else:
            frame["promo_value"] = 1

    return enriched_frames


def main():
    total = 0
    rejects = 0

    # 确定输入源：优先 V2，不存在则回退 fallback
    source_file = ANALYSIS_V2
    if not os.path.exists(source_file):
        if ANALYSIS_FALLBACK and os.path.exists(ANALYSIS_FALLBACK):
            source_file = ANALYSIS_FALLBACK
            print(f"[INFO] V2 不存在，回退到 fallback: {ANALYSIS_FALLBACK}")
        else:
            print(f"[ERR] 输入文件不存在: {ANALYSIS_V2}")
            print(f"      请先运行 multi_frame_sample.py 生成 V2 数据")
            return

    os.makedirs(os.path.dirname(ANALYSIS_V3), exist_ok=True)

    # ── Pass 1: 读取 + enrich，收集所有帧 ──
    enriched = []
    with open(source_file, "r", encoding="utf-8") as src:
        for line in src:
            if not line.strip():
                continue
            parsed_line = parse_line(line)
            if not parsed_line:
                continue
            frame_id, data, parsed = parsed_line
            data = enrich(data, parsed)
            enriched.append((frame_id, data))

    # ── Pass 2: 分位数熔断 ──
    all_data = [d for _, d in enriched]
    all_data = apply_percentile_braking(all_data)

    # 熔断后重算 cut_role（基于新 hook_value）
    for _, data in enriched:
        hv = data.get("hook_value", 1)
        if hv >= 5:
            data["cut_role"] = "hook"
        elif hv >= 4:
            data["cut_role"] = "climax"
        elif hv >= 3:
            data["cut_role"] = "rise"
        elif hv >= 2:
            data["cut_role"] = "setup"
        else:
            data["cut_role"] = "ending"

    # ── 写入 ──
    total = 0
    rejects = 0
    with open(ANALYSIS_V3, "w", encoding="utf-8") as out, \
         open(REJECTS_OUT, "w", encoding="utf-8") as rej:
        for frame_id, data in enriched:
            timestamp = float(data.get("timestamp", 0))
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            out.write(f"{frame_id} {timestamp}s {payload}\n")
            total += 1
            if not data.get("usable", True):
                rejects += 1
                rej.write(f"{frame_id} {timestamp}s {data.get('reject_reason', '无')} {data.get('hint', '')}\n")
    print(f"V3 written: {ANALYSIS_V3}")
    print(f"Rejects written: {REJECTS_OUT}")
    print(f"Total: {total}, rejects: {rejects}, usable: {total - rejects}")


if __name__ == "__main__":
    main()
