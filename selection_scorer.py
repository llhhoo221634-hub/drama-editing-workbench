"""
selection_scorer.py — 片段评分、叙事加权、数据加载与分析
"""
import os, json, re

from edit_utils import parse_vision_line
from config import get_engine_config, get_project_config
from molecule_types import MOLECULE_TYPES, FUSION_WEIGHTS, clip_event_text, is_high_conflict_clip, molecular_score

# ── Config (self-contained to avoid circular imports) ──
_cfg = get_engine_config()
_project = get_project_config()
ANALYSIS_FILE = _project["analysis_v3"]
ANALYSIS_FALLBACK = _project["analysis_fallback"]
OUTPUT_DIR = _project["molecular_dir"]

# ── Cache ──
_STORY_PROFILE_CACHE = None


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

    # V1数据仅保留V2未覆盖的集（去重：同ep且时间差<5s）
    v1_added = 0
    for c in v1_clips:
        ep = int(c.get('ep', '0') or 0)
        ts = c.get('time', 0)
        is_dup = False
        for v2_ep, v2_ts in v2_keys:
            if v2_ep == ep and abs(v2_ts - ts) < 5:
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


def molecular_fusion_score(clip, mol_type):
    """类型感知分数融合：归一化各字段后按分子类型权重加权求和，缺失字段权重按比例重分配。"""
    w = FUSION_WEIGHTS.get(mol_type, {})
    if not w:
        return molecular_score(clip, mol_type) / 250.0
    ms = molecular_score(clip, mol_type)
    raw = {
        "molecular": ms / 250.0,
        "aesthetic_score": (clip.get("aesthetic_score", 0) or 0) / 10.0,
        "hook_value": (clip.get("hook_value", 0) or 0) / 5.0,
        "promo_value": (clip.get("promo_value", 0) or 0) / 5.0,
    }
    present = ["molecular"]
    if clip.get("aesthetic_score") is not None:
        present.append("aesthetic_score")
    if clip.get("hook_value") is not None:
        present.append("hook_value")
    if clip.get("promo_value") is not None:
        present.append("promo_value")
    total_w = sum(w[k] for k in present)
    if total_w <= 0:
        return 0.0
    adjusted = {k: w[k] / total_w for k in present}
    result = sum(adjusted[k] * raw[k] for k in present)
    nb = narrative_boost(clip)
    # Layer 0: golden_quotes boost for quote_rhythm
    if mol_type == 'quote_rhythm' and golden_quote_boost(clip):
        result += 0.15
    return result + 0.08 * nb


# ── Layer 0: 叙事加权 (Narrative Weighting) ──

def _load_full_profile():
    """读取 story_profile.json 完整数据（带缓存）。"""
    global _STORY_PROFILE_CACHE
    if _STORY_PROFILE_CACHE is not None:
        return _STORY_PROFILE_CACHE
    sp_path = os.path.join(os.path.dirname(OUTPUT_DIR), "story_profile.json")
    if not os.path.exists(sp_path):
        _STORY_PROFILE_CACHE = {}
        return {}
    try:
        with open(sp_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _STORY_PROFILE_CACHE = data
        return data
    except Exception:
        _STORY_PROFILE_CACHE = {}
        return {}


def load_story_profile():
    """读取 story_profile.json，返回情节节点列表。支持 key_plot_nodes / 情节节点 字段。"""
    data = _load_full_profile()
    nodes = data.get("key_plot_nodes", data.get("情节节点", []))
    if not isinstance(nodes, list):
        nodes = []
    return nodes


def load_golden_quotes():
    """读取 story_profile.json 中的 golden_quotes 字段，返回金句列表。
    每条: {episode, timestamp, text, type, impact}"""
    data = _load_full_profile()
    quotes = data.get("golden_quotes", [])
    if not isinstance(quotes, list):
        return []
    return quotes


def golden_quote_boost(clip):
    """clip 的 ep 和 time 附近 ±5s 是否有 impact≥3 的 golden_quote。"""
    quotes = load_golden_quotes()
    if not quotes:
        return False
    try:
        ep = int(clip.get('ep', '0') or 0)
    except (ValueError, TypeError):
        return False
    t = clip.get('time', 0)
    for q in quotes:
        try:
            q_ep = int(re.search(r'\d+', str(q.get('episode', ''))).group())
        except Exception:
            continue
        if q_ep != ep:
            continue
        ts = str(q.get('timestamp', ''))
        try:
            parts = ts.split(':')
            q_time = int(parts[0]) * 60 + int(parts[1])
        except Exception:
            continue
        if abs(t - q_time) <= 5 and q.get('impact', 0) >= 3:
            return True
    return False


def load_character_relationships():
    """读取 story_profile.json 中的 人物关系 / character_relationships 字段。"""
    data = _load_full_profile()
    chars = data.get("character_relationships", data.get("人物关系", []))
    if not isinstance(chars, list):
        return []
    return chars


def _parse_ep_range(ep_range_str):
    """解析 "EP01-EP10" / "1-10" 等格式 → (min_ep, max_ep)。"""
    import re
    nums = re.findall(r'\d+', str(ep_range_str or ''))
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return 0, 0


def narrative_boost(clip):
    """基于 story_profile 的叙事加权：高潮段 +1，高潮段内冲突事件再 +1。"""
    nodes = load_story_profile()
    if not nodes:
        return 0
    try:
        ep = int(clip.get('ep', '0') or 0)
    except (ValueError, TypeError):
        return 0
    event_text = clip_event_text(clip)
    boost = 0
    for node in nodes:
        ep_lo, ep_hi = _parse_ep_range(node.get("集数范围", ""))
        if not (ep_lo <= ep <= ep_hi):
            continue
        stage = node.get("阶段", "")
        if any(kw in stage for kw in ["高潮", "决战", "反转"]):
            boost += 1
        if any(kw in event_text for kw in ["对峙", "冲突", "打斗"]) and \
           any(kw in stage for kw in ["高潮", "决战", "反转"]):
            boost += 1
    return boost
