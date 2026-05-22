"""
剪辑工具集 v1.0 — 缓存 / 评分 / QA / 并行 / 日志
可被 auto_script_gen.py 导入，也可独立使用。
"""
import json, os, sys, subprocess, time, hashlib, shutil, logging, re, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

# ── 日志 ──
def get_logger(name="edit_utils", level=logging.INFO):
    """获取统一logger, 首次调用时配置格式"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname).1s] %(message)s',
            datefmt='%H:%M:%S'
        ))
        logger.addHandler(h)
        logger.setLevel(level)
    return logger

log = get_logger()

# ── 路径配置 ──
SCRIPT_DIR = Path(__file__).parent
CACHE_DIR = SCRIPT_DIR / ".edit_cache"
CACHE_DIR.mkdir(exist_ok=True)

# ── ffmpeg / ffprobe 路径(从 config.json 或环境变量读取) ──
def _load_tool_config():
    cf = SCRIPT_DIR / "config.json"
    if cf.exists():
        return json.loads(cf.read_text('utf-8'))
    return {}
_tcfg = _load_tool_config()
FFMPEG  = _tcfg.get("ffmpeg", os.environ.get("FFMPEG", "ffmpeg"))
FFPROBE = _tcfg.get("ffprobe", os.environ.get("FFPROBE", "ffprobe"))

def load_engine_config():
    """返回原始引擎配置。"""
    return dict(_tcfg)


def load_project_config(config=None):
    """从 config.json 读取通用项目配置，兼容旧字段。"""
    cfg = dict(config or _tcfg)
    project = dict(cfg.get("project") or {})

    media_dir = project.get("media_dir") or cfg.get("media_dir") or ""
    work_dir = project.get("work_dir") or cfg.get("work_dir") or str(SCRIPT_DIR / "_work")
    project_name = project.get("project_name") or cfg.get("project_name") or Path(media_dir).name or "drama_project"
    analysis_v2 = project.get("analysis_v2") or cfg.get("analysis_v2") or os.path.join(work_dir, "analysis_v2.txt")
    analysis_v3 = project.get("analysis_v3") or cfg.get("analysis_v3") or os.path.join(work_dir, "analysis_v3.txt")
    analysis_fallback = project.get("analysis_fallback") or cfg.get("analysis_fallback") or os.path.join(work_dir, "analysis.txt")
    frames_dir = project.get("frames_dir") or cfg.get("frames_dir") or os.path.join(work_dir, "_frames_v2")
    molecular_dir = project.get("molecular_dir") or cfg.get("molecular_dir") or os.path.join(work_dir, "_work_molecular")
    bgm = project.get("bgm") or cfg.get("bgm") or cfg.get("default_bgm") or ""
    episode_glob = project.get("episode_glob") or cfg.get("episode_glob") or "*.mp4"
    episode_count = project.get("episode_count") or cfg.get("episode_count")
    name_template = project.get("episode_name_template") or cfg.get("episode_name_template") or "{ep}.mp4"

    return {
        "project_name": project_name,
        "media_dir": media_dir,
        "work_dir": work_dir,
        "analysis_v2": analysis_v2,
        "analysis_v3": analysis_v3,
        "analysis_fallback": analysis_fallback,
        "frames_dir": frames_dir,
        "molecular_dir": molecular_dir,
        "bgm": bgm,
        "episode_glob": episode_glob,
        "episode_count": episode_count,
        "episode_name_template": name_template,
        "config": cfg,
    }


def episode_filename(ep, name_template=None):
    """根据统一模板生成集数文件名。"""
    template = name_template or "{ep}.mp4"
    ep_int = int(ep)
    return template.format(ep=ep_int, ep02=f"{ep_int:02d}", ep03=f"{ep_int:03d}")


def get_audio_analysis_config(config=None):
    """读取音频分析配置并补默认值。"""
    cfg = dict(config or _tcfg)
    audio_cfg = dict(cfg.get("audio_analysis") or {})
    return {
        "enabled": bool(audio_cfg.get("enabled", False)),
        "whisper_model": audio_cfg.get("whisper_model", "medium"),
        "window_seconds": float(audio_cfg.get("window_seconds", 2.5) or 2.5),
        "speech_peak_chars": float(audio_cfg.get("speech_peak_chars", 14) or 14),
        "energy_peak_threshold": float(audio_cfg.get("energy_peak_threshold", 0.72) or 0.72),
    }


def audio_cache_dir(work_dir=None):
    """音频缓存目录。"""
    base = Path(work_dir) if work_dir else (SCRIPT_DIR / "_work")
    cache_dir = base / "_audio_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def episode_audio_cache_paths(ep, work_dir=None):
    """返回单集音频缓存路径。"""
    ep_tag = f"ep{int(ep):02d}"
    base = audio_cache_dir(work_dir)
    return {
        "wav": str(base / f"{ep_tag}.wav"),
        "vtt": str(base / f"{ep_tag}.vtt"),
        "json": str(base / f"{ep_tag}.audio.json"),
    }


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

# ═══════════════════════════════════════════
# 1. VisionCache — 审片结果缓存（断点续传）
# ═══════════════════════════════════════════

class VisionCache:
    """基于 MD5 的 vision.js 调用缓存。同一张图 + 同一个 prompt 不重复调 API。"""

    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir or CACHE_DIR) / "vision"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _hash(self, image_path, prompt):
        """图片内容 + prompt → MD5"""
        h = hashlib.md5()
        with open(image_path, 'rb') as f:
            h.update(f.read())
        h.update(prompt.encode('utf-8'))
        return h.hexdigest()

    def get(self, image_path, prompt):
        key = self._hash(image_path, prompt)
        cf = self.cache_dir / f"{key}.json"
        if cf.exists():
            return json.loads(cf.read_text('utf-8'))
        return None

    def set(self, image_path, prompt, result):
        key = self._hash(image_path, prompt)
        cf = self.cache_dir / f"{key}.json"
        cf.write_text(json.dumps({
            "image": str(image_path), "prompt": prompt,
            "result": result, "cached_at": time.time()
        }, ensure_ascii=False, indent=2), 'utf-8')

    def stats(self):
        files = list(self.cache_dir.glob("*.json"))
        return {"cached_frames": len(files), "dir": str(self.cache_dir)}


# ═══════════════════════════════════════════
# 2. 审片数据解析 + 智能评分
# ═══════════════════════════════════════════

# ── 场景类型分类及稀有度 ──
# 稀有度 1.0 = 常见, 2.0 = 少见(更有剪辑价值)
SCENE_RARITY = {
    "战斗": 2.0, "打斗": 2.0, "搏斗": 2.0,
    "下葬": 1.8, "入棺": 1.8, "活埋": 2.0,
    "祭坛": 1.7, "祭祀": 1.7, "作法": 1.8,
    "对峙": 1.2, "冲突": 1.3,
    "独白": 1.5, "特写": 1.3, "惊恐": 1.4,
    "夜葬": 1.6, "抬棺": 1.5,
    "诡异": 1.2, "恐怖": 1.2,
    "葬礼": 1.1, "仪式": 1.3,
    "空镜": 0.8, "日常": 0.5, "对话": 1.0,
    "群像": 1.1, "凝视": 1.2,
}

def _extract_json_object(text):
    """从混合文本/markdown code fence 中提取首个JSON对象。"""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r'^```(?:json)?\s*', '', stripped)
        stripped = re.sub(r'\s*```$', '', stripped)
    try:
        return json.loads(stripped)
    except Exception:
        pass

    start = stripped.find('{')
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(stripped)):
            ch = stripped[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = stripped[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break
        start = stripped.find('{', start + 1)
    return None


def parse_vision_line(line):
    """
    解析单行 vision.js 输出 → 结构化 dict。
    兼容三种格式:
      A: "情绪：4" / "情绪评分：4" (旧版极简)
      B: "描述文本…情绪：4。类型：恐怖/诡异/对峙。" (旧版长格式)
      C: '```json{"shot":"特写","emo":4,"event":"葬礼",...}```' (V2 JSON)
    """
    import re
    result = {"emotion": 3, "scene_types": [], "has_content": False,
              "desc_clean": "", "desc_length": 0, "format": "legacy"}

    # ── 检测 V2 JSON 格式 ──
    v2 = _extract_json_object(line)
    if isinstance(v2, dict) and "shot" in v2:
        try:
            result["emotion"] = int(v2.get("emo", 3))
            result["format"] = "v2"
            # 提取场景类型: event + event_subtype + mood + shot
            event = v2.get("event", "")
            event_subtype = v2.get("event_subtype", "")
            mood = v2.get("mood", "")
            for value in [event, event_subtype, mood]:
                if value:
                    for item in re.split(r'[;；,，/]', str(value)):
                        item = item.strip()
                        if item and item != '无': result["scene_types"].append(item)
            shot = v2.get("shot", "")
            if shot:
                result["scene_types"].append(shot)
            # chars 里有人物信息
            chars_raw = v2.get("chars", "")
            if isinstance(chars_raw, list):
                visible_faces = [c for c in chars_raw if c.get("face") not in ["背影", "模糊", "无人", ""]]
                result["faces"] = len(visible_faces)
                chars_text = "; ".join(
                    f"{c.get('id','?')}({c.get('gender','?')},{c.get('face','?')},{c.get('action','?')},{c.get('emo','?')})"
                    for c in chars_raw
                )
            else:
                chars_text = str(chars_raw or "")
                result["faces"] = chars_text.count(';') + (1 if chars_text and '人' not in chars_text[:2] else 0)
            # hint 作为内容描述
            hint = v2.get("hint", "")
            props = v2.get("props", "")
            result["desc_clean"] = f"{hint} | {props}" if props and props != '无' else hint
            result["desc_length"] = len(result["desc_clean"])
            result["has_content"] = len(hint) > 2
            # V2扩展字段
            result["scene"] = v2.get("scene", "")
            result["light"] = v2.get("light", "")
            result["event_conf"] = v2.get("event_conf", "推测")
            result["continuity"] = v2.get("continuity", "")
            result["time_pct"] = v2.get("time_pct", v2.get("timestamp", 0))
            result["timestamp"] = v2.get("timestamp", result["time_pct"])
            result["ep"] = v2.get("ep", result.get("ep", ""))
            result["usable"] = v2.get("usable", True)
            result["reject_reason"] = v2.get("reject_reason", "无")
            result["visual_quality"] = int(v2.get("visual_quality", 3) or 3)
            result["face_quality"] = v2.get("face_quality", "")
            result["action_level"] = int(v2.get("action_level", 1) or 1)
            result["dialogue_visible"] = bool(v2.get("dialogue_visible", False))
            result["subtitle_text"] = v2.get("subtitle_text", "")
            result["event_subtype"] = event_subtype
            result["chars_text"] = chars_text
            # 音频增强字段 (summarize_audio_window)
            result["audio_energy"] = int(v2.get("audio_energy", 1) or 1)
            result["speech_density"] = int(v2.get("speech_density", 1) or 1)
            result["has_speech_peak"] = bool(v2.get("has_speech_peak", False))
            result["beat_nearby"] = bool(v2.get("beat_nearby", False))
            result["transcript_excerpt"] = (v2.get("transcript_excerpt", "") or "").strip()
            result["dialogue_anchor"] = v2.get("dialogue_anchor", "none") or "none"
            # V3 剪辑决策字段 (build_analysis_v3.py enrich 平铺到顶层)
            result["promo_value"] = int(v2.get("promo_value", 1) or 1)
            result["hook_value"] = int(v2.get("hook_value", 1) or 1)
            result["cut_role"] = v2.get("cut_role", "unknown")
            result["best_cut"] = v2.get("best_cut", "before_action")
            result["pre_roll"] = float(v2.get("pre_roll", 1.0) or 1.0)
            result["post_roll"] = float(v2.get("post_roll", 1.5) or 1.5)
            result["suggested_duration"] = float(v2.get("suggested_duration", 2.5) or 2.5)
            result["action_direction"] = v2.get("action_direction", "静止")
            result["emotion_trend"] = v2.get("emotion_trend", "稳定")
            result["_v2"] = v2  # 保留完整V2/V3数据
            result["scene_types"] = list(set(result["scene_types"]))
            return result
        except (ValueError, TypeError, KeyError):
            pass  # 回退到旧解析

    # ── 旧格式解析 ──
    content_part = line
    score_part = ""

    m_emo = re.search(r'情绪[评分：:]*\s*(\d+)', line)
    if m_emo:
        result["emotion"] = int(m_emo.group(1))
        idx = m_emo.start()
        content_part = line[:idx].strip()
        score_part = line[idx:]

    result["has_content"] = len(content_part) > 3
    result["desc_clean"] = content_part
    result["desc_length"] = len(content_part)

    # 从 "类型：X/X/X" 显式标签
    m_type = re.search(r'类型[：:]\s*([^\s。，]+)', score_part)
    if m_type:
        types = [t.strip() for t in m_type.group(1).split('/')]
        result["scene_types"].extend(types)

    # 从括号标注 "(诡异/对峙)"
    m_paren = re.findall(r'[（(]([^）)]+)[）)]', score_part)
    for p in m_paren:
        types = [t.strip() for t in p.split('/')]
        result["scene_types"].extend(types)

    # 从内容描述中匹配已知场景关键词
    for kw in SCENE_RARITY:
        if kw in content_part and kw not in result["scene_types"]:
            result["scene_types"].append(kw)

    # 去重
    result["scene_types"] = list(set(result["scene_types"]))

    # ── 对话检测(内容描述里的关键词) ──
    dialogue_kw = ['说', '问', '喊', '叫', '道', '言', '语', '诉', '答', '吼', '骂', '曰']
    result["dialogue_lines"] = sum(1 for kw in dialogue_kw if kw in content_part)
    # 如果内容本身是直接引语(不包含描述性词)，通过双重特征判断:
    #   A) 有对话标点(?!吗呢吧啊) 或
    #   B) 有人称代词(我你他她) + 内容短(<30字，不含描述词)
    desc_words = ['情绪', '场景', '光线', '氛围', '画面', '类型', '描述', '表情', '动作']
    if result["dialogue_lines"] == 0 and content_part and len(content_part) >= 3:
        if not any(kw in content_part for kw in desc_words):
            dialogue_punct = set('？！吗呢吧啊呀嘛啦哦噢嗯哎')
            pronouns = set('我你他她它咱')
            has_punct = any(c in content_part for c in dialogue_punct)
            has_pronoun = any(c in content_part for c in pronouns)
            is_short = len(content_part) <= 30
            if has_punct or (has_pronoun and is_short):
                result["dialogue_lines"] = 1

    # ── 人脸/人物检测 ──
    face_kw = ['人', '众', '者', '员', '男', '女', '老', '少', '汉', '子', '爷', '娘']
    result["faces"] = min(5, sum(1 for kw in face_kw if kw in content_part))

    return result


def score_clip(clip, weights=None):
    """
    多维度片段评分 (0-100)。clip 可以是从 parse_vision_line() 输出的 dict，
    也可以是包含以下字段的 dict:
      emotion: int 1-5
      scene_types: list[str]
      has_content: bool
      desc_length: int
      dialogue_lines: int
      faces: int
    """
    w = weights or {
        "emotion": 4.0,       # 情绪权重最高
        "scene_rare": 2.0,    # 稀有场景加分
        "dialogue": 2.0,      # 有台词 > 无台词
        "content": 1.5,       # 有实质描述 vs 只有评分
        "faces": 1.0,         # 有人物 > 空镜
        "desc_len": 0.3,      # 描述长度(log压缩，几乎不影响)
    }

    score = 0.0
    emotion = clip.get('emotion', 3)

    # ① 情绪: 非线性，让高分情绪拉开差距
    emotion_map = {1: 0, 2: 5, 3: 15, 4: 40, 5: 70}
    score += emotion_map.get(emotion, 15) * w['emotion']

    # ② 场景稀有度: 加权求和，上限30
    scene_types = clip.get('scene_types', [])
    rarity_sum = sum(SCENE_RARITY.get(st, 1.0) for st in scene_types)
    score += min(30, rarity_sum * 5) * w['scene_rare']

    # ③ 对话: 每行台词最多+8分，上限40
    score += min(40, clip.get('dialogue_lines', 0) * 8) * w['dialogue']

    # ④ 内容质量: 有实质描述(不只是"情绪：4") +12分
    if clip.get('has_content', False):
        score += 12 * w['content']

    # ⑤ 人物: 每人+4分，上限16
    score += min(16, clip.get('faces', 0) * 4) * w['faces']

    # ⑥ 描述长度: log压缩 + 上限5分 (解决长短描述不公)
    import math
    desc_len = clip.get('desc_length', 0)
    score += min(5, math.log(max(1, desc_len)) * 1.5) * w['desc_len']

    return round(score, 1)


def apply_time_weight(clips, total_duration=None):
    """
    短剧黄金公式时间加权:
      前3秒  ×3.0 (钩子必须强)
      前10秒 ×2.0 (快速建立)
      后20%  ×1.5 (结尾钩子)
      中段   ×1.0
    修改 clips 的 _score 字段 (需先调用 score_clip)。

    total_duration: 估算总时长(秒)，不传则基于片段数估算
    """
    if not clips:
        return clips

    if total_duration is None:
        total_duration = len(clips) * 7  # 假设每段7秒

    # 按 episode+time 排序(保持时间线)
    sorted_clips = sorted(clips, key=lambda c: (
        int(c.get('ep', '0') or 0),
        c.get('time', 0)
    ))

    for i, c in enumerate(sorted_clips):
        # 用片段在序列中的位置比例估算在成片中的时间位置
        position_ratio = i / max(1, len(sorted_clips) - 1)
        estimated_time = position_ratio * total_duration

        if estimated_time < 3:
            boost = 3.0
        elif estimated_time < 10:
            boost = 2.0
        elif estimated_time > total_duration * 0.8:
            boost = 1.5
        else:
            boost = 1.0

        orig_score = c.get('_score', 50)
        c['_score'] = round(orig_score * boost, 1)
        c['_time_boost'] = boost

    return sorted_clips


# ── 废镜头关键词(提案1: 走路/发呆/吃饭/空镜) ──
BORING_SHOT_KEYWORDS = [
    '走路', '行走', '步行', '散步', '踱步',
    '吃饭', '用餐', '喝水', '喝茶',
    '发呆', '愣神', '出神', '呆滞看',
    '空镜', '远景空', '无人', '无人物',
    '睡觉', '躺', '休息',
    '收拾', '整理', '打扫',
]

def is_boring_shot(clip):
    """判断是否为废镜头"""
    desc = clip.get('desc_clean', '') + clip.get('desc', '')
    if not desc:
        return False
    # 检查是否只有情绪标签无实质内容
    if not clip.get('has_content', True) and clip.get('dialogue_lines', 0) == 0:
        return True
    # 检查废镜头关键词
    return any(kw in desc for kw in BORING_SHOT_KEYWORDS)


def filter_boring_shots(clips):
    """过滤废镜头，返回过滤后列表和被过滤列表"""
    keep = []
    removed = []
    for c in clips:
        if is_boring_shot(c):
            removed.append(c)
        else:
            keep.append(c)
    return keep, removed


def rank_clips(clips, top_n=20, prefer_diverse_episodes=True):
    """
    对片段列表评分并排序。
    prefer_diverse_episodes=True 时，同一集只保留最高分的 2 个片段。
    同时避免场景类型重复过多。
    """
    for c in clips:
        c['_score'] = score_clip(c)

    ranked = sorted(clips, key=lambda c: c['_score'], reverse=True)

    if not prefer_diverse_episodes:
        return ranked[:top_n]

    # 多样性筛选：同一集最多2个 + 场景类型不重复
    result = []
    ep_count = {}
    used_scenes = set()

    for c in ranked:
        ep = str(c.get('ep', c.get('episode', '?')))
        ep_count[ep] = ep_count.get(ep, 0)

        if ep_count[ep] >= 2:
            continue  # 同集已达上限

        # 场景去重：如果该片段的所有场景类型都已经出现过，跳过
        clip_scenes = set(c.get('scene_types', []))
        if clip_scenes and clip_scenes.issubset(used_scenes) and len(result) >= 5:
            continue  # 前5个之后的才做场景去重

        result.append(c)
        ep_count[ep] += 1
        used_scenes.update(clip_scenes)

        if len(result) >= top_n:
            break

    return result


# ═══════════════════════════════════════════
# 3. AutoQA — 自动音频质量检测
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
# 3.5 事实校验 + 音频闪避
# ═══════════════════════════════════════════

def fact_check_selection(selected_clips, source_analysis_file):
    """
    防LLM幻觉: 校验选中的片段描述在原始审片数据中是否有依据。
    返回: (passed, warnings_list)
    """
    # 读取原始数据所有描述文本
    source_text = ""
    try:
        with open(source_analysis_file, 'r', encoding='utf-8') as f:
            source_text = f.read()
    except:
        return True, []

    # 高风险虚构词(短剧LLM常见幻觉)
    hallucination_risk_words = [
        '亲子鉴定', 'DNA', '遗嘱', '股权转让', '破产',
        '癌症', '绝症', '失忆', '车祸', '坠楼',
        '扇巴掌', '耳光', '下跪', '磕头',
    ]

    warnings = []
    for clip in selected_clips:
        desc = clip.get('desc_clean', '') + clip.get('desc', '')
        for word in hallucination_risk_words:
            if word in desc and word not in source_text:
                warnings.append(
                    f"可能的幻觉: '{word}' 出现在片段描述中, "
                    f"但审片数据中未找到依据 (EP{clip.get('ep','?')} {clip.get('time','?')}s)"
                )

    return len(warnings) == 0, warnings


def build_audio_ducking_filter(dialogue_segments, total_dur, bgm_vol=0.15, duck_vol=0.04):
    """
    生成 ffmpeg 音频闪避滤镜字符串。
    在有对话的时段自动降低 BGM 音量，对话结束后恢复。

    dialogue_segments: [(start, end), ...] 对话时间段
    total_dur: 总时长(秒)
    bgm_vol: BGM正常音量
    duck_vol: 闪避时BGM音量(对话期间)

    返回 ffmpeg -filter_complex 用的滤镜字符串。
    """
    if not dialogue_segments:
        return f"volume={bgm_vol}"

    # 生成音量包络: 对话期间低音量，其他时间正常
    # 使用 aeval 生成动态音量曲线
    parts = []
    for start, end in dialogue_segments:
        parts.append(f"between(t,{start},{end})")
    # 如果有对话段, 构建条件表达式
    if parts:
        expr = '+'.join(parts)
        return (
            f"volume={bgm_vol}:eval=frame,"
            f"aformat=sample_fmts=fltp,"
            f"aeval="
            f"if({expr}, {duck_vol}/{bgm_vol}, 1)"
            f":c=same"
        )

    return f"volume={bgm_vol}"


def check_audio_quality(video_path, whisper_model="medium"):
    """
    对成品视频做自动质检：
      - 检测对话重叠（相邻段间隔 < 0.2s）
      - 检测截断（segment 结束在 clip 边界 ±0.3s）
      - 检测长静音（> 3s 无语音）
    返回 (passed, issues_list)
    """
    import tempfile
    issues = []

    # 提取音频
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    subprocess.run(
        [FFMPEG, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", audio_path],
        capture_output=True
    )

    # Whisper 转录
    try:
        result = subprocess.run(
            ["whisper", audio_path, "--model", whisper_model, "--language", "zh",
             "--output_format", "vtt", "--output_dir", os.path.dirname(audio_path)],
            capture_output=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        os.unlink(audio_path)
        return False, ["Whisper 超时(>3min)"]

    # 解析 VTT
    vtt_path = audio_path.replace(".wav", ".vtt")
    segments = []
    if os.path.exists(vtt_path):
        for line in open(vtt_path, 'r', encoding='utf-8').readlines():
            if '-->' in line:
                parts = line.strip().split(' --> ')
                start = _parse_vtt_time(parts[0])
                end = _parse_vtt_time(parts[1])
                segments.append((start, end))

    # 检查项
    for i in range(1, len(segments)):
        gap = segments[i][0] - segments[i-1][1]
        if 0 < gap < 0.15:  # 几乎无缝 → 可能重叠
            issues.append(f"对话可能重叠 @ {segments[i-1][1]:.1f}s → {segments[i][0]:.1f}s (间隔{gap:.2f}s)")

    # 检查长静音
    for i in range(1, len(segments)):
        gap = segments[i][0] - segments[i-1][1]
        if gap > 3.0:
            issues.append(f"长静音 {gap:.1f}s @ {segments[i-1][1]:.1f}s → {segments[i][0]:.1f}s")

    # 检查结尾截断
    if segments:
        dur = float(subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path], capture_output=True
        ).stdout.decode('utf-8').strip())
        last_end = segments[-1][1]
        if dur - last_end < 0.3 and dur - last_end > 0.05:
            issues.append(f"结尾截断风险: 最后语音@{last_end:.1f}s, 视频结束@{dur:.1f}s")

    # 清理
    os.unlink(audio_path)
    for f in [vtt_path]:
        if os.path.exists(f):
            os.unlink(f)

    passed = len(issues) == 0
    return passed, issues


def _parse_vtt_time(ts):
    """00:00:01.234 → 1.234"""
    ts = ts.strip()
    if '.' in ts:
        main, frac = ts.rsplit('.', 1)
    else:
        main, frac = ts, '0'
    parts = main.split(':')
    if len(parts) == 3:
        h, m, s = parts
    else:
        h, m, s = 0, parts[0], parts[1] if len(parts) > 1 else 0
    return int(h) * 3600 + int(m) * 60 + int(s) + float(f"0.{frac}")


def parse_whisper_vtt(vtt_path):
    """解析 whisper VTT，返回带文本的分段列表。"""
    segments = []
    if not vtt_path or not os.path.exists(vtt_path):
        return segments

    lines = [line.rstrip('\n') for line in open(vtt_path, 'r', encoding='utf-8').readlines()]
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if '-->' not in line:
            i += 1
            continue
        parts = line.split(' --> ')
        if len(parts) != 2:
            i += 1
            continue
        start = _parse_vtt_time(parts[0])
        end = _parse_vtt_time(parts[1])
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = ' '.join(text_lines).strip()
        if text:
            segments.append({
                'start': round(start, 3),
                'end': round(end, 3),
                'text': text,
                'chars': len(text),
                'duration': round(max(0.0, end - start), 3),
            })
        i += 1
    return segments


def summarize_audio_window(segments, center_time, window_seconds=2.5,
                           speech_peak_chars=14, energy_peak_threshold=0.72):
    """
    基于 Whisper 分段近似生成时间窗内的音频特征。
    不依赖 librosa，先用对白密度/字数变化做代理特征。
    """
    center_time = float(center_time or 0)
    half = max(0.5, float(window_seconds) / 2.0)
    start = max(0.0, center_time - half)
    end = center_time + half
    overlaps = []
    for seg in segments or []:
        ov_start = max(start, seg['start'])
        ov_end = min(end, seg['end'])
        if ov_end <= ov_start:
            continue
        ratio = (ov_end - ov_start) / max(0.001, seg['end'] - seg['start'])
        overlaps.append((seg, ratio, ov_start, ov_end))

    excerpt_parts = []
    covered = 0.0
    total_chars = 0.0
    peak_chars = 0.0
    near_boundary = False
    for seg, ratio, ov_start, ov_end in overlaps:
        excerpt_parts.append(seg['text'])
        chars_here = seg['chars'] * ratio
        total_chars += chars_here
        peak_chars = max(peak_chars, chars_here)
        covered += (ov_end - ov_start)
        if abs(seg['start'] - center_time) <= 0.45 or abs(seg['end'] - center_time) <= 0.45:
            near_boundary = True

    window_span = max(0.5, end - start)
    coverage_ratio = min(1.0, covered / window_span)
    chars_per_second = total_chars / window_span
    speech_density = max(1, min(5, int(round(chars_per_second / 2.2)) + 1 if total_chars > 0 else 1))
    audio_energy = max(1, min(5, int(round((coverage_ratio * 2.2) + (chars_per_second / 3.5))) + 1 if total_chars > 0 else 1))
    has_speech_peak = peak_chars >= float(speech_peak_chars)
    beat_nearby = has_speech_peak or near_boundary or coverage_ratio >= float(energy_peak_threshold)

    if not overlaps:
        conf = 'low'
    elif len(overlaps) >= 2 or coverage_ratio >= 0.55:
        conf = 'high'
    else:
        conf = 'medium'

    excerpt = ' '.join(dict.fromkeys([p for p in excerpt_parts if p])).strip()
    excerpt = re.sub(r'\s+', ' ', excerpt)[:80]

    return {
        'audio_energy': int(audio_energy),
        'speech_density': int(speech_density),
        'has_speech_peak': bool(has_speech_peak),
        'beat_nearby': bool(beat_nearby),
        'transcript_excerpt': excerpt,
        'audio_event_conf': conf,
        'dialogue_anchor': 'boundary' if near_boundary else ('dense_speech' if overlaps else 'none'),
        'speech_coverage': round(coverage_ratio, 3),
        'chars_per_second': round(chars_per_second, 3),
    }


# ═══════════════════════════════════════════
# 4. ParallelRunner — 通用并行任务执行器
# ═══════════════════════════════════════════

class ParallelRunner:
    """
    通用并行任务执行器。用法:

      runner = ParallelRunner(workers=4)
      tasks = [
        {"id": "clip1", "cmd": ["ffmpeg", "-i", "src.mp4", ...]},
        {"id": "clip2", "cmd": ["ffmpeg", "-i", "src2.mp4", ...]},
      ]
      results = runner.run_commands(tasks)
      # → {"clip1": {"ok": True, "elapsed": 2.3}, "clip2": {"ok": False, "stderr": "..."}}

    也支持 Python 函数:

      def cut_clip(ep, start, dur):
          ...
      results = runner.run_funcs([
          {"id": "v1", "fn": cut_clip, "args": (1, 38, 6)},
      ])
    """

    def __init__(self, workers=4, verbose=True):
        self.workers = min(workers, 8)  # 最多 8 并发，避免 I/O 瓶颈
        self.verbose = verbose

    def run_commands(self, tasks, timeout_per_task=120):
        """
        tasks: [{"id": str, "cmd": list, "timeout": int(optional)}, ...]
        返回: {task_id: {"ok": bool, "elapsed": float, "stdout": str, "stderr": str}}
        """
        results = {}
        total = len(tasks)
        done = 0

        def _run_one(task):
            tid = task["id"]
            t0 = time.time()
            try:
                r = subprocess.run(
                    task["cmd"],
                    capture_output=True,
                    timeout=task.get("timeout", timeout_per_task)
                )
                elapsed = round(time.time() - t0, 1)
                ok = r.returncode == 0
                # 安全解码，避免 GBK/UTF-8 乱码
                def safe_decode(b):
                    try: return b.decode('utf-8')[-200:]
                    except: return str(b[-200:])
                return tid, {"ok": ok, "elapsed": elapsed,
                             "stdout": safe_decode(r.stdout), "stderr": safe_decode(r.stderr)}
            except subprocess.TimeoutExpired:
                return tid, {"ok": False, "elapsed": timeout_per_task, "stderr": "Timeout"}
            except Exception as e:
                return tid, {"ok": False, "elapsed": round(time.time() - t0, 1), "stderr": str(e)}

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_run_one, t): t["id"] for t in tasks}
            for f in as_completed(futures):
                tid, result = f.result()
                results[tid] = result
                done += 1
                if self.verbose:
                    status = "OK" if result["ok"] else "FAIL"
                    print(f"  [{done}/{total}] {status} {tid} ({result['elapsed']}s)")

        return results

    def run_funcs(self, tasks, timeout_per_task=300):
        """
        tasks: [{"id": str, "fn": callable, "args": tuple, "kwargs": dict}, ...]
        返回: {task_id: {"ok": bool, "result": any, "elapsed": float}}
        """
        results = {}
        total = len(tasks)
        done = 0

        def _run_one(task):
            tid = task["id"]
            t0 = time.time()
            try:
                fn = task["fn"]
                args = task.get("args", ())
                kwargs = task.get("kwargs", {})
                r = fn(*args, **kwargs)
                elapsed = round(time.time() - t0, 1)
                return tid, {"ok": True, "result": r, "elapsed": elapsed}
            except Exception as e:
                return tid, {"ok": False, "result": None, "elapsed": round(time.time() - t0, 1), "error": str(e)}

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_run_one, t): t["id"] for t in tasks}
            for f in as_completed(futures):
                tid, result = f.result()
                results[tid] = result
                done += 1
                if self.verbose:
                    status = "✓" if result["ok"] else "✗"
                    print(f"  [{done}/{total}] {status} {tid} ({result['elapsed']}s)")

        return results


# ═══════════════════════════════════════════
# 便捷函数：并行切片段（最常用的并行场景）
# ═══════════════════════════════════════════

def parallel_cut_clips(clip_specs, source_dir, output_dir,
                       grade_filter="eq=contrast=1.15:saturation=0.8:brightness=-0.05",
                       workers=4, name_func=None):
    """
    并行切割多个片段。

    clip_specs: [{"id": "v1_临终", "ep": "01", "start": 38, "dur": 6}, ...]
    source_dir: 源视频目录
    output_dir: 输出目录
    grade_filter: ffmpeg 调色滤镜
    workers: 并发数
    name_func: 可选, ep→filename映射函数 (e.g. lambda ep: f"{int(ep)}.mp4")

    返回: ParallelRunner 的 results dict
    """
    os.makedirs(output_dir, exist_ok=True)
    tasks = []

    for c in clip_specs:
        if name_func:
            src_name = name_func(c['ep'])
        else:
            src_name = f"第{c['ep']}集.mp4"
        src = os.path.join(source_dir, src_name)
        out = os.path.join(output_dir, f"{c['id']}.mp4")
        cmd = [
            FFMPEG, "-y",
            "-ss", str(c["start"]), "-t", str(c["dur"]),
            "-i", src,
            "-vf", grade_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out
        ]
        tasks.append({"id": c["id"], "cmd": cmd})

    runner = ParallelRunner(workers=workers)
    print(f"并行切割 {len(tasks)} 个片段 (workers={workers}):")
    return runner.run_commands(tasks)


def parallel_whisper(audio_paths, output_dir, model="medium", language="zh", workers=3):
    """
    并行跑多个 Whisper 转录。

    audio_paths: [{"id": "ep16", "path": "/tmp/ep16.wav"}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    tasks = []
    for a in audio_paths:
        cmd = [
            "whisper", a["path"],
            "--model", model, "--language", language,
            "--output_format", "vtt", "--output_dir", output_dir
        ]
        tasks.append({"id": a["id"], "cmd": cmd, "timeout": 300})

    runner = ParallelRunner(workers=workers)
    print(f"并行 Whisper {len(tasks)} 段音频 (workers={workers}):")
    return runner.run_commands(tasks)


# ═══════════════════════════════════════════
# 自检：运行此文件可验证各模块
# ═══════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 50)
    print(" edit_utils.py — 模块自检")
    print("=" * 50)

    # 1. 缓存
    vc = VisionCache()
    print(f"\n[VisionCache] {vc.stats()}")

    # 2. 评分
    test_clips = [
        {"ep": "01", "emotion": 4, "dialogue_lines": 3, "faces": 2, "scene_change": True, "desc_length": 25},
        {"ep": "01", "emotion": 3, "dialogue_lines": 1, "faces": 1, "scene_change": False, "desc_length": 8},
        {"ep": "30", "emotion": 5, "dialogue_lines": 8, "faces": 3, "scene_change": True, "desc_length": 40},
        {"ep": "79", "emotion": 4, "dialogue_lines": 4, "faces": 1, "scene_change": False, "desc_length": 30},
    ]
    ranked = rank_clips(test_clips, top_n=3)
    print(f"\n[ClipScorer] Top 3:")
    for i, c in enumerate(ranked):
        print(f"  {i+1}. EP{c['ep']} score={c['_score']} emotion={c['emotion']} dialogue={c['dialogue_lines']}")

    # 3. 并行 (dry-run)
    runner = ParallelRunner(workers=2)
    print(f"\n[ParallelRunner] workers={runner.workers}, 准备就绪")

    # 4. QA
    print(f"\n[AutoQA] 对视频文件运行: check_audio_quality('path/to/video.mp4')")

    print("\n✅ 所有模块加载正常")
