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

SKILL_DIR = Path(r"E:\技能skills\剪辑skills_backup_2")
sys.path.insert(0, str(SKILL_DIR))

from edit_utils import parse_vision_line

ANALYSIS_V2 = r"E:\视频\一品布衣\analysis_v2.txt"
ANALYSIS_V3 = r"E:\视频\一品布衣\analysis_v3.txt"
REJECTS_OUT = r"E:\视频\一品布衣\analysis_v3_rejects.txt"

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
    if reason != "无":
        return reason
    if any(k in blob for k in ["许可证", "发行许可证", "网络剧片"]):
        return "片头"
    if any(k in blob for k in ["剧终", "完结", "片尾"]):
        return "片尾"
    if any(k in blob for k in ["纯白", "白屏", "大面积过曝"]):
        return "白屏"
    if any(k in blob for k in ["纯黑", "黑屏"]):
        return "黑屏"
    if any(k in blob for k in ["纯文字", "文字卡", "竖排文字"]):
        return "纯文字"
    if data.get("event_conf") == "模糊" or "严重模糊" in blob:
        return "模糊"
    if data.get("shot") == "空镜" and data.get("visual_quality", 3) < 4:
        return "空镜"
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
    usable = reject_reason == "无" and data.get("usable", True) is not False
    visual_quality = clamp(data.get("visual_quality", 3))
    action_level = clamp(data.get("action_level", 1))
    emotion = clamp(data.get("emo", parsed.get("emotion", 3)))
    data["event_subtype"] = infer_event_subtype(data, blob, reject_reason)
    data["reject_reason"] = reject_reason
    data["usable"] = usable
    data["event_conf"] = "模糊" if reject_reason != "无" else data.get("event_conf", "可见")

    promo_value = emotion + action_level + visual_quality
    if any(k in blob for k in HIGH_CONFLICT):
        promo_value += 3
    if any(k in blob for k in EMOTIONAL):
        promo_value += 2
    if any(k in blob for k in VISUAL):
        promo_value += 1
    if not usable:
        promo_value -= 8
    promo_value = clamp(round(promo_value / 4))

    hook_value = emotion + action_level
    if any(k in blob for k in HIGH_CONFLICT):
        hook_value += 4
    if visual_quality < 3 or not usable:
        hook_value -= 5
    hook_value = clamp(round(hook_value / 3))

    emotion_value = emotion
    if any(k in blob for k in EMOTIONAL):
        emotion_value += 1
    emotion_value = clamp(emotion_value)

    action_value = action_level
    if any(k in blob for k in HIGH_CONFLICT):
        action_value += 1
    action_value = clamp(action_value)

    visual_value = visual_quality
    if any(k in blob for k in VISUAL):
        visual_value += 1
    visual_value = clamp(visual_value)

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
        "conflict_side": infer_conflict_side(blob),
        "cut_role": cut_role,
        "best_cut": best_cut,
        "pre_roll": pre_roll,
        "post_roll": post_roll,
        "suggested_duration": duration,
        "source_format": parsed.get("format", "legacy"),
    })
    return data


def main():
    total = 0
    rejects = 0
    os.makedirs(os.path.dirname(ANALYSIS_V3), exist_ok=True)
    with open(ANALYSIS_V2, "r", encoding="utf-8") as src, \
         open(ANALYSIS_V3, "w", encoding="utf-8") as out, \
         open(REJECTS_OUT, "w", encoding="utf-8") as rej:
        for line in src:
            if not line.strip():
                continue
            parsed_line = parse_line(line)
            if not parsed_line:
                continue
            frame_id, data, parsed = parsed_line
            data = enrich(data, parsed)
            timestamp = int(float(data.get("timestamp", 0)))
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
