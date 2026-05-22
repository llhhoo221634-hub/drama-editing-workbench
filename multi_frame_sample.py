"""
multi_frame_sample.py — Phase 4: 多帧采样 + 场景检测 + V2标记

用 ffmpeg scene detection 定位关键帧截取点，调用千问 VL 逐帧生成
V2 结构化标记，输出 analysis_v2.txt 供后续选片引擎使用。

核心改进:
  - 场景检测代替盲目的百分比采样（只在画面真正变化处截帧）
  - V2标记体系: scene/light/event_conf/continuity/chars结构体
  - 相邻帧 hash 去重，避免浪费 API
  - hint 只写可见内容，不推测剧情

用法:
  python multi_frame_sample.py                    # 全105集
  python multi_frame_sample.py --dry-run          # 仅场景检测，不调API
  python multi_frame_sample.py --eps 1,7,52       # 指定集数测试
  python multi_frame_sample.py --scene-threshold 0.25  # 调整场景检测灵敏度
"""
import os, sys, json, time, hashlib, subprocess, re, base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

from edit_utils import (
    load_engine_config,
    load_project_config,
    episode_filename,
    get_audio_analysis_config,
    episode_audio_cache_paths,
    parse_whisper_vtt,
    summarize_audio_window,
    read_json,
    write_json,
)

# ── Config ──
_cfg = load_engine_config()
_project = load_project_config(_cfg)
SOURCE_DIR = _project["media_dir"]
OUTPUT_DIR = _project["frames_dir"]
ANALYSIS_OUT = _project["analysis_v2"]
OLD_ANALYSIS = _project["analysis_fallback"]
PROJECT_NAME = _project["project_name"]
EPISODE_NAME_TEMPLATE = _project["episode_name_template"]

# ffmpeg/ffprobe paths
FFMPEG = _cfg.get("ffmpeg", "ffmpeg")
FFPROBE = _cfg.get("ffprobe", "ffprobe")

# ── Vision API config (Phase 0.5) ──
_vision_cfg = _cfg.get("vision", {}) if isinstance(_cfg.get("vision"), dict) else {}
API_KEY = _vision_cfg.get("api_key") or _cfg.get("dashscope_api_key", "")
API_BASE = _vision_cfg.get("base_url") or _cfg.get("dashscope_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
VISION_MODEL = _vision_cfg.get("model") or _cfg.get("vision_model", "qwen-vl-plus")
_VISION_MAX_SHORT_EDGE = int(_vision_cfg.get("max_short_edge", 640) or 640)
_VISION_CONCURRENCY = int(_vision_cfg.get("concurrency", 3) or 3)
_VISION_MAX_RETRIES = int(_vision_cfg.get("max_retries", 3) or 3)
_VISION_TIMEOUT = int(_vision_cfg.get("timeout", 45) or 45)
_AUDIO_CFG = get_audio_analysis_config(_cfg)

# ── Vision API session & circuit breaker state ──
_api_session = requests.Session()
_api_session.headers.update({"Content-Type": "application/json"})
if API_KEY:
    _api_session.headers.update({"Authorization": f"Bearer {API_KEY}"})

_circuit_breaker = {"consecutive_failures": 0, "paused_until": 0.0}

def _resolve_episode_source(ep_num):
    """按统一命名模板定位集数文件，兼容少量旧命名。"""
    candidates = [episode_filename(ep_num, EPISODE_NAME_TEMPLATE)]
    ep_int = int(ep_num)
    legacy = [f"{ep_int}.mp4", f"{ep_int:02d}.mp4", f"第{ep_int}集.mp4"]
    for name in legacy:
        if name not in candidates:
            candidates.append(name)
    for name in candidates:
        src = os.path.join(SOURCE_DIR, name)
        if os.path.exists(src):
            return src
    return ""
# ── 采样策略 ──
# 场景检测为每集找到变化点，然后按集长分档限制最大帧数
MAX_FRAMES = {
    "short":  4,   # <90s
    "medium": 7,   # 90-180s
    "long":   10,  # >180s
}

# V2 标记 Prompt（趋势标签版：不要求绝对数字评分，只输出定性标签+描述）
VISION_PROMPT = """你是一位短剧宣发剪辑师。这三张图来自同一段连续视频(时间顺序:前-中-后)。以中间帧为主要评估对象，结合前后帧的时序变化，用JSON描述。

返回格式:
{
  "shot": "镜头类型(特写/近景/中景/全景/空镜)",
  "event": "主事件(冲突/对峙/日常/打斗/跪地/威胁/其他)",
  "event_subtype": "细分(受伤/倒地/抓扯/持械/怒吼/哭泣/文字卡/空镜/奇幻爆发/无)",
  "event_conf": "可见/模糊",
  "face_quality": "正脸/侧脸/半面/背影/模糊/无人",
  "dialogue_visible": true/false,
  "subtitle_text": "字幕",
  "usable": true/false,
  "reject_reason": "无/片头/片尾/纯文字/白屏/黑屏/模糊/空镜/低质量",
  "action_direction": "增强/持续/静止/减弱",
  "emotion_trend": "上升/稳定/下降/爆发",
  "scene": "场景",
  "light": "光线",
  "chars": [{"id":"简称","gender":"男/女","face":"正脸/侧脸/半面/背影","action":"动作","emo":"情绪"}],
  "hint": "一句话描述中间帧可见内容。禁止推测词(似乎/可能/大概/仿佛/看起来像)",
  "props": "道具",
  "mood": "氛围"
}

[event 强互斥约束 - 必须遵守]
- 只有三帧中明确出现身体对抗、武器挥舞、摔砸物品、演员面部极度扭曲或流泪时，event才能标注为"冲突"或"对峙"
- 任何仅有两人站立、面对面说话、表情严肃但无肢体动作的画面，必须严格标记为"日常/对话"
- 空镜/无人画面 event 必须写"其他"

[空镜保护规则]
- 若中间帧为空镜(shot=空镜)或face_quality=无人: hint中[动作方向]写静止,[情绪趋势]写稳定

[硬性规则]
1. hint禁止推测词(似乎/可能/大概/仿佛/看起来像)
2. 口型只写嘴唇微张/张嘴/闭嘴
3. 纯文字/白屏/黑屏/片头许可证/严重模糊: usable=false
4. 主体模糊时event_conf写模糊"""



def get_episode_duration(ep_num):
    """用 ffprobe 获取集数时长(秒)"""
    src = _resolve_episode_source(ep_num)
    if not src:
        return 0
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", src], capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        return float(r.stdout.strip())
    except:
        return 0


def detect_scenes(ep_num, threshold=0.3):
    """
    用 ffmpeg scene detection 找画面变化点。
    返回: [(timestamp, score), ...] 按时间排序
    """
    src = _resolve_episode_source(ep_num)
    if not src:
        return []

    # 用 select filter 检测场景变化
    cmd = [
        FFMPEG, "-i", src,
        "-filter_complex", f"select='gt(scene,{threshold})',metadata=print",
        "-vsync", "vfr", "-f", "null", "-"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)

    scenes = []
    for line in r.stderr.split('\n'):
        # ffmpeg scene detection outputs: "lavfi.scene_score=0.xx"
        m = re.search(r'pts_time:([\d.]+)', line)
        if m:
            t = float(m.group(1))
            # 取最近的 scene_score
            # ffmpeg 输出格式较复杂，简化处理
            if t > 0.5 and (not scenes or t - scenes[-1][0] > 2.0):
                scenes.append((round(t, 1), 0))
    return scenes


def detect_scenes_via_thumbnail(ep_num, threshold=0.3):
    """
    更可靠的场景检测: 生成缩略图网格，用 ffmpeg thumbnail filter。
    返回关键帧时间戳列表。
    """
    src = _resolve_episode_source(ep_num)
    if not src:
        return []

    dur = get_episode_duration(ep_num)
    if dur <= 0:
        return []

    # 方法: 用 scene change detection 的 pts 输出
    # 这是最直接的方式
    cmd = [
        FFMPEG, "-i", src,
        "-vf", f"select='gt(scene\\,{threshold})',showinfo",
        "-vsync", "vfr", "-f", "null", "-"
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    except subprocess.TimeoutExpired:
        return []

    timestamps = []
    for line in (r.stderr or '').split('\n'):
        m = re.search(r'pts_time:([\d.]+)', line)
        if m:
            t = float(m.group(1))
            # 过滤太近的时间点
            if t > 0.3 and (not timestamps or t - timestamps[-1] > 3.0):
                timestamps.append(round(t, 1))

    return timestamps


def classify_episode(duration):
    """按时长分档"""
    if duration < 90:
        return "short"
    elif duration < 180:
        return "medium"
    else:
        return "long"


def select_key_timestamps(scene_ts, ep_class, duration):
    """
    从场景检测结果中选取关键帧时间戳。
    策略:
      1. 场景检测点（画面变化处）
      2. 与旧 analysis 事件时间点 ±3s 窗口内如果有场景点，加密保留
      3. 首帧(2s) + 尾帧(dur-2s) 必含
      4. 总数不超过该档最大帧数
    """
    max_frames = MAX_FRAMES[ep_class]
    selected = set()

    # 首帧
    selected.add(round(2.0, 1))
    # 尾帧
    if duration > 5:
        selected.add(round(duration - 2, 1))

    # 场景检测点（按时间均匀选取）
    if scene_ts:
        # 分成 max_frames-2 个时间桶
        buckets = max_frames - 2
        if buckets <= 0:
            buckets = 2
        bucket_size = duration / buckets
        bucket_filled = [False] * buckets

        for ts in scene_ts:
            bucket_idx = min(int(ts / bucket_size), buckets - 1)
            if not bucket_filled[bucket_idx]:
                # 取该桶内第一个场景点
                if 1.0 < ts < duration - 1:
                    selected.add(round(ts, 1))
                    bucket_filled[bucket_idx] = True

        # 填充空桶
        for i, filled in enumerate(bucket_filled):
            if not filled:
                t = round((i + 0.5) * bucket_size, 1)
                if 1.0 < t < duration - 1:
                    selected.add(t)

    else:
        # 无场景检测结果，用百分比
        if ep_class == "short":
            pcts = [0.25, 0.50, 0.75]
        elif ep_class == "medium":
            pcts = [0.15, 0.30, 0.50, 0.70, 0.85]
        else:
            pcts = [0.10, 0.22, 0.35, 0.50, 0.60, 0.75, 0.90]
        for p in pcts:
            t = round(duration * p, 1)
            if 1.0 < t < duration - 1:
                selected.add(t)

    # 限制总数 + 合并太近的时间点 (最小间隔5s)
    result = sorted(selected)
    merged = []
    for t in result:
        if not merged or t - merged[-1] >= 5.0:
            merged.append(t)
        elif merged and t - merged[-1] < 5.0:
            # 保留中间值
            merged[-1] = round((merged[-1] + t) / 2, 1)
    return merged[:max_frames]


def extract_frame(ep_num, timestamp, output_path):
    """用 ffmpeg 在指定时间戳截取一帧"""
    src = _resolve_episode_source(ep_num)
    if not src:
        return False

    r = subprocess.run([
        FFMPEG, "-y", "-ss", str(timestamp), "-i", src,
        "-vframes", "1", "-q:v", "3",
        output_path
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 1000


def frame_hash(filepath):
    """计算图片的简单hash（用于去重）"""
    h = hashlib.md5()
    with open(filepath, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


def frame_quality_probe(filepath):
    """用ffmpeg信号统计粗筛白屏/黑屏/低对比帧。"""
    if not os.path.exists(filepath):
        return {"usable": False, "reject_reason": "低质量", "mean": 0, "stdev": 0}
    try:
        r = subprocess.run([
            FFMPEG, "-hide_banner", "-i", filepath,
            "-vf", "signalstats,metadata=print:file=-",
            "-frames:v", "1", "-f", "null", "-"
        ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
        text = (r.stdout or "") + "\n" + (r.stderr or "")
        yavg = re.search(r'lavfi.signalstats.YAVG=([\d.]+)', text)
        yd = re.search(r'lavfi.signalstats.YDIF=([\d.]+)', text)
        mean = float(yavg.group(1)) if yavg else 128.0
        stdev = float(yd.group(1)) if yd else 10.0
    except Exception:
        return {"usable": True, "reject_reason": "无", "mean": 128, "stdev": 10}

    if mean >= 245:
        return {"usable": False, "reject_reason": "白屏", "mean": mean, "stdev": stdev}
    if mean <= 8:
        return {"usable": False, "reject_reason": "黑屏", "mean": mean, "stdev": stdev}
    if stdev <= 0.8 and mean <= 20:
        return {"usable": False, "reject_reason": "低质量", "mean": mean, "stdev": stdev}
    if stdev <= 0.8 and mean >= 235:
        return {"usable": False, "reject_reason": "低质量", "mean": mean, "stdev": stdev}
    return {"usable": True, "reject_reason": "无", "mean": mean, "stdev": stdev}


def sanitize_hint(text):
    """移除识图结果里的弱推测词 + 正则提取 hint 中的决策标签。"""
    if not text:
        return "", {}
    # 提取标签
    tags = {}
    m_action = re.search(r'\[动作[:：]\s*([^\]]+)\]', text)
    if m_action:
        tags['action_direction'] = m_action.group(1).strip()
    m_emotion = re.search(r'\[情绪趋势[:：]\s*([^\]]+)\]', text)
    if m_emotion:
        tags['emotion_trend'] = m_emotion.group(1).strip()
    m_pos = re.search(r'\[适合位置[:：]\s*([^\]]+)\]', text)
    if m_pos:
        tags['promo_position'] = m_pos.group(1).strip()
    # 清理标签后的纯文本
    clean = re.sub(r'\[[^\]]+\]', '', text).strip()
    # 推测词替换
    replacements = {
        "似乎在": "",
        "似在": "",
        "似乎": "",
        "可能": "",
        "大概": "",
        "仿佛": "",
        "看起来像": "",
        "意味着": "",
        "正在说话": "嘴唇微张",
        "在说话": "嘴唇微张",
    }
    for old, new in replacements.items():
        clean = clean.replace(old, new)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean, tags


def infer_quality_fields(v2_data, frame_probe=None):
    """补齐旧模型缺失的可用性/质量字段。多帧模式下应用校准偏移。"""
    probe = frame_probe or {"usable": True, "reject_reason": "无"}
    hint, hint_tags = sanitize_hint(v2_data.get("hint", ""))
    v2_data["hint"] = hint

    reject_reason = v2_data.get("reject_reason", "无") or "无"
    event_subtype = v2_data.get("event_subtype", "") or "无"
    event_conf = v2_data.get("event_conf", "推测")
    shot = v2_data.get("shot", "")
    chars = v2_data.get("chars", [])
    chars_text = json.dumps(chars, ensure_ascii=False) if isinstance(chars, list) else str(chars)
    text_blob = " ".join(str(v2_data.get(k, "")) for k in ["hint", "mood", "scene", "props", "subtitle_text"]) + " " + chars_text
    visual_quality = int(v2_data.get("visual_quality", 3) or 3)

    subtitle_text = (v2_data.get("subtitle_text", "") or "").strip()
    subtitle_len = len(subtitle_text)

    if not probe.get("usable", True) and visual_quality <= 2:
        reject_reason = probe.get("reject_reason", "低质量")
    elif any(k in text_blob for k in ["许可证", "发行许可证", "网络剧片", "完", "剧终"]):
        reject_reason = "片头" if "许可证" in text_blob or "网络剧片" in text_blob else "片尾"
    elif any(k in text_blob for k in ["纯白", "白色背景", "大面积过曝"]):
        reject_reason = "白屏"
    elif any(k in text_blob for k in ["纯黑", "黑色背景"]):
        reject_reason = "黑屏"
    elif shot == "空镜" or event_subtype == "空镜":
        reject_reason = "空镜"
    elif subtitle_len >= 12 and any(k in text_blob for k in ["纯文字", "竖排", "字幕卡"]):
        reject_reason = "纯文字"
    elif event_conf == "模糊" and visual_quality <= 2:
        reject_reason = "模糊"
    elif reject_reason == "低质量" and visual_quality >= 3:
        reject_reason = "无"

    usable = v2_data.get("usable", reject_reason == "无")
    if isinstance(usable, str):
        usable = usable.lower() not in ["false", "0", "no", "否"]
    usable = bool(usable) and reject_reason == "无"

    if reject_reason != "无":
        event_conf = "模糊"

    # ── 从 JSON 字段读趋势标签（模型直接输出）──
    action_dir = (v2_data.get("action_direction", "") or "").strip()
    if not action_dir or action_dir not in ('增强', '持续', '静止', '减弱'):
        action_dir = "静止"
    emotion_trend = (v2_data.get("emotion_trend", "") or "").strip()
    if not emotion_trend or emotion_trend not in ('上升', '稳定', '下降', '爆发'):
        emotion_trend = "稳定"

    # emo: 由趋势标签组合推导
    emo_map = {
        ('爆发', '增强'): 5, ('爆发', '持续'): 4, ('爆发', '静止'): 3,
        ('上升', '增强'): 4, ('上升', '持续'): 3, ('上升', '静止'): 2,
        ('稳定', '增强'): 3, ('稳定', '持续'): 2, ('稳定', '静止'): 1,
        ('下降', '增强'): 3, ('下降', '持续'): 2, ('下降', '静止'): 1,
    }
    derived_emo = emo_map.get((emotion_trend, action_dir), 2)
    v2_data["emo"] = derived_emo

    # action_level: 由动作方向推导
    action_map = {'增强': 4, '持续': 2, '静止': 1, '减弱': 1}
    derived_action = action_map.get(action_dir, 2)
    v2_data["action_level"] = derived_action

    # visual_quality: probe兜底，默认3，不再让VL打分
    if "visual_quality" not in v2_data:
        if reject_reason in ["白屏", "黑屏", "模糊", "低质量"]:
            v2_data["visual_quality"] = 1
        elif reject_reason in ["片头", "片尾", "纯文字", "空镜"]:
            v2_data["visual_quality"] = 2
        else:
            v2_data["visual_quality"] = 3

    if "face_quality" not in v2_data:
        if "正脸" in chars_text or "可见" in chars_text:
            v2_data["face_quality"] = "正脸"
        elif "侧脸" in chars_text or "半面" in chars_text:
            v2_data["face_quality"] = "侧脸"
        elif "背影" in chars_text:
            v2_data["face_quality"] = "背影"
        elif not chars_text.strip() or chars_text in ["[]", ""]:
            v2_data["face_quality"] = "无人"
        else:
            v2_data["face_quality"] = "模糊"

    if "dialogue_visible" not in v2_data:
        v2_data["dialogue_visible"] = any(k in chars_text + hint for k in ["嘴唇微张", "张嘴", "嘴微张"])
    if "subtitle_text" not in v2_data:
        v2_data["subtitle_text"] = ""
    if "event_subtype" not in v2_data or not v2_data.get("event_subtype"):
        if reject_reason in ["片头", "片尾", "纯文字", "白屏", "黑屏", "空镜"]:
            v2_data["event_subtype"] = reject_reason
        elif any(k in text_blob for k in ["血", "伤", "痛苦"]):
            v2_data["event_subtype"] = "受伤"
        elif any(k in text_blob for k in ["跪", "倒地", "躺"]):
            v2_data["event_subtype"] = "倒地"
        elif any(k in text_blob for k in ["怒吼", "呐喊", "张口怒"]):
            v2_data["event_subtype"] = "怒吼"
        elif any(k in text_blob for k in ["发光", "能量", "光束"]):
            v2_data["event_subtype"] = "奇幻爆发"
        else:
            v2_data["event_subtype"] = "无"

    v2_data["usable"] = usable
    v2_data["reject_reason"] = reject_reason
    v2_data["event_conf"] = event_conf

    # ── 注入趋势标签 ──
    v2_data["action_direction"] = action_dir
    v2_data["emotion_trend"] = emotion_trend

    return v2_data


def _compress_image_for_api(image_path):
    """压缩图片到 API 友好尺寸，返回 base64 data URL。
    将短边缩放到 max_short_edge（默认 640px），JPEG 质量 85%。"""
    from PIL import Image
    import io

    try:
        img = Image.open(image_path)
        w, h = img.size
        short_edge = min(w, h)
        if short_edge > _VISION_MAX_SHORT_EDGE:
            scale = _VISION_MAX_SHORT_EDGE / short_edge
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
        img_bytes = buf.getvalue()
    except Exception:
        # PIL 不可用或图片损坏，回退到原始文件
        with open(image_path, 'rb') as f:
            img_bytes = f.read()

    b64 = base64.b64encode(img_bytes).decode('utf-8')
    return f"data:image/jpeg;base64,{b64}"


def _api_call_with_retry(payload):
    """带指数退避重试 + 简化熔断的 API 调用。"""
    global _circuit_breaker

    # 熔断检查
    now = time.time()
    if _circuit_breaker["paused_until"] > now:
        wait = _circuit_breaker["paused_until"] - now
        time.sleep(wait)
        _circuit_breaker["paused_until"] = 0.0
        _circuit_breaker["consecutive_failures"] = 0

    url = f"{API_BASE}/chat/completions"

    for attempt in range(_VISION_MAX_RETRIES):
        try:
            resp = _api_session.post(url, json=payload, timeout=_VISION_TIMEOUT)

            if resp.status_code == 200:
                _circuit_breaker["consecutive_failures"] = 0
                return resp.json()

            # 429 Rate limit — 退避重试
            if resp.status_code == 429:
                backoff = 2 ** attempt + 1
                time.sleep(backoff)
                continue

            # 5xx Server error — 退避重试
            if resp.status_code >= 500:
                backoff = 2 ** attempt + 0.5
                time.sleep(backoff)
                continue

            # 4xx 其他错误（如 400/401/403）— 不重试
            _circuit_breaker["consecutive_failures"] += 1
            return {"error": f"API_ERROR_{resp.status_code}", "status": resp.status_code}

        except requests.exceptions.Timeout:
            if attempt < _VISION_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            _circuit_breaker["consecutive_failures"] += 1
            return {"error": "API_TIMEOUT"}

        except requests.exceptions.ConnectionError:
            if attempt < _VISION_MAX_RETRIES - 1:
                time.sleep(2 ** attempt + 1)
                continue
            _circuit_breaker["consecutive_failures"] += 1
            return {"error": "API_CONNECTION_ERROR"}

        except Exception as e:
            _circuit_breaker["consecutive_failures"] += 1
            return {"error": f"API_ERROR: {str(e)}"}

    # 所有重试用尽
    _circuit_breaker["consecutive_failures"] += 1

    # 简化熔断：连续 5 次失败 → 暂停 30s
    if _circuit_breaker["consecutive_failures"] >= 5:
        _circuit_breaker["paused_until"] = time.time() + 30.0
        print(f"    [熔断] 连续{_circuit_breaker['consecutive_failures']}次失败，暂停30s")

    return {"error": "API_MAX_RETRIES_EXCEEDED"}


def call_vision_api(image_paths):
    """调用千问 VL API 分析图片，返回 JSON 文本。
    image_paths: 单路径(字符串)或三帧列表(列表)。
    Phase 0.5: 图片压缩 + Session复用 + 重试退避 + 熔断。
    Phase A: 多帧时序输入（三帧条带 → Image List）。"""
    if not API_KEY:
        return None

    # 统一为列表
    if isinstance(image_paths, str):
        image_paths = [image_paths]

    # 构建 content 数组
    content = []
    for p in image_paths:
        data_url = _compress_image_for_api(p)
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    content.append({"type": "text", "text": VISION_PROMPT})

    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "max_tokens": 800 if len(image_paths) > 1 else 600,
    }

    result = _api_call_with_retry(payload)

    if "error" in result:
        return result["error"]

    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content


def extract_json_from_response(text):
    """
    从 API 返回文本中提取 JSON。
    千问可能返回 ```json{...}``` 或纯 JSON 或混合文本。
    """
    if not text:
        return None

    # 尝试 ```json...``` 格式
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except:
            pass

    # 尝试直接 JSON
    m = re.search(r'\{[\s\S]*"shot"[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except:
            pass

    # 尝试任意 JSON 对象
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if "shot" in parsed:  # 至少要有 shot 字段
                return parsed
        except:
            pass

    return None


def flatten_v2_for_output(v2_data, ep_num, timestamp, frame_idx, prev_frame_id):
    """
    将 V2 结构化标记扁平化为 analysis_v2.txt 的兼容格式。
    同时保留 V2 扩展字段。
    """
    # 扁平化 chars 数组
    chars_list = v2_data.get("chars", [])
    if isinstance(chars_list, list):
        chars_str = "; ".join(
            f"{c.get('id','?')}({c.get('gender','?')},{c.get('face','?')},{c.get('action','?')},{c.get('emo','?')})"
            for c in chars_list
        )
    else:
        chars_str = str(chars_list)

    flat = {
        "shot": v2_data.get("shot", "中景"),
        "chars": chars_str,
        "event": v2_data.get("event", "其他"),
        "event_subtype": v2_data.get("event_subtype", "无"),
        "emo": int(v2_data.get("emo", 3)),
        "action_level": int(v2_data.get("action_level", 1)),
        "visual_quality": int(v2_data.get("visual_quality", 3)),
        "face_quality": v2_data.get("face_quality", "模糊"),
        "dialogue_visible": bool(v2_data.get("dialogue_visible", False)),
        "subtitle_text": v2_data.get("subtitle_text", ""),
        "audio_energy": int(v2_data.get("audio_energy", 1)),
        "speech_density": int(v2_data.get("speech_density", 1)),
        "has_speech_peak": bool(v2_data.get("has_speech_peak", False)),
        "beat_nearby": bool(v2_data.get("beat_nearby", False)),
        "transcript_excerpt": v2_data.get("transcript_excerpt", ""),
        "audio_event_conf": v2_data.get("audio_event_conf", "low"),
        "dialogue_anchor": v2_data.get("dialogue_anchor", "none"),
        "speech_coverage": float(v2_data.get("speech_coverage", 0.0)),
        "chars_per_second": float(v2_data.get("chars_per_second", 0.0)),
        "usable": bool(v2_data.get("usable", True)),
        "reject_reason": v2_data.get("reject_reason", "无"),
        "hint": v2_data.get("hint", ""),
        "props": v2_data.get("props", "无"),
        "mood": v2_data.get("mood", ""),
        # V2 扩展字段
        "scene": v2_data.get("scene", ""),
        "light": v2_data.get("light", ""),
        "event_conf": v2_data.get("event_conf", "推测"),
        "continuity": prev_frame_id or "none",
        "timestamp": round(timestamp, 1),
        "time_pct": round(timestamp, 1),
    }
    # 多帧决策标签（从 hint 正则提取）
    for tag_key in ['action_direction', 'emotion_trend']:
        if tag_key in v2_data and v2_data[tag_key]:
            flat[tag_key] = v2_data[tag_key]

    frame_id = f"ep{int(ep_num):02d}_f{frame_idx}"
    time_str = f"{int(timestamp)}s"
    json_str = json.dumps(flat, ensure_ascii=False, separators=(',', ':'))

    return f"{frame_id} {time_str} {json_str}", frame_id


def dedup_frames(frames_dir, ep_num):
    """对同集内相邻帧做 hash 去重，删除相似度>90%的重复帧"""
    ep_prefix = f"ep{int(ep_num):02d}_f"
    frames = sorted(
        [f for f in os.listdir(frames_dir) if f.startswith(ep_prefix) and f.endswith('.jpg')],
        key=lambda x: int(re.search(r'f(\d+)', x).group(1))
    )

    to_remove = set()
    for i in range(len(frames) - 1):
        if frames[i] in to_remove:
            continue
        h1 = frame_hash(os.path.join(frames_dir, frames[i]))
        h2 = frame_hash(os.path.join(frames_dir, frames[i + 1]))
        if h1 == h2:
            to_remove.add(frames[i + 1])

    for f in to_remove:
        os.remove(os.path.join(frames_dir, f))

    return [f for f in frames if f not in to_remove]


def load_old_analysis():
    """加载旧 analysis.txt 的事件时间点（用于加密采样）"""
    old_data = {}  # {ep: [time, event, emo]}
    if not os.path.exists(OLD_ANALYSIS):
        return old_data

    with open(OLD_ANALYSIS, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(' ', 2)
            if len(parts) < 3:
                continue
            ep_str = parts[0].replace('ep', '')
            time_str = parts[1].replace('s', '')
            try:
                ep = int(ep_str)
                t = int(time_str)
                if ep not in old_data:
                    old_data[ep] = []
                old_data[ep].append(t)
            except:
                pass
    return old_data


def _ensure_episode_audio_features(ep_num, source_path):
    """抽取/缓存单集 Whisper 音频特征。"""
    if not _AUDIO_CFG.get("enabled"):
        return []
    if not source_path or not os.path.exists(source_path):
        return []

    cache_paths = episode_audio_cache_paths(ep_num, _project["work_dir"])
    cached = read_json(cache_paths["json"], default=None)
    if cached and os.path.exists(cache_paths["vtt"]):
        segments = cached.get("segments")
        if segments:
            return segments
        return parse_whisper_vtt(cache_paths["vtt"])

    os.makedirs(os.path.dirname(cache_paths["wav"]), exist_ok=True)
    wav_cmd = [
        FFMPEG, "-y", "-i", source_path,
        "-vn", "-ac", "1", "-ar", "16000",
        cache_paths["wav"]
    ]
    wav_run = subprocess.run(
        wav_cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=600,
    )
    if wav_run.returncode != 0 or not os.path.exists(cache_paths["wav"]):
        return []

    whisper_cmd = [
        "whisper", cache_paths["wav"],
        "--model", _AUDIO_CFG.get("whisper_model", "medium"),
        "--language", "zh",
        "--output_format", "vtt",
        "--output_dir", os.path.dirname(cache_paths["vtt"]),
    ]
    try:
        whisper_run = subprocess.run(
            whisper_cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return []

    if whisper_run.returncode != 0 or not os.path.exists(cache_paths["vtt"]):
        return []

    segments = parse_whisper_vtt(cache_paths["vtt"])
    write_json(cache_paths["json"], {
        "ep": int(ep_num),
        "source": source_path,
        "generated_at": time.time(),
        "segments": segments,
    })
    return segments


def process_episode(ep_num, scene_threshold=0.3, dry_run=False):
    """
    处理单集: 场景检测 → 关键帧选取 → 截帧 → Vision分析 → 输出V2标记
    """
    print(f"  [EP{ep_num:02d}] 开始处理...")

    dur = get_episode_duration(ep_num)
    if dur <= 0:
        print(f"  [EP{ep_num:02d}] [WARN] 无法获取时长，跳过")
        return []

    ep_class = classify_episode(dur)
    print(f"  [EP{ep_num:02d}] 时长 {dur:.0f}s ({ep_class}), 场景检测中...")

    # 场景检测
    scene_ts = detect_scenes_via_thumbnail(ep_num, scene_threshold)
    print(f"  [EP{ep_num:02d}] 场景检测: {len(scene_ts)} 个变化点")

    # 选取关键帧时间戳
    key_ts = select_key_timestamps(scene_ts, ep_class, dur)
    print(f"  [EP{ep_num:02d}] 关键帧: {len(key_ts)} 个 @ {key_ts}")

    if dry_run:
        return [{"ep": ep_num, "dur": dur, "class": ep_class,
                 "scenes": len(scene_ts), "key_ts": key_ts}]

    # 截帧（主帧 + 邻帧 ±1.5s）
    ep_dir = os.path.join(OUTPUT_DIR, f"ep{ep_num:02d}")
    os.makedirs(ep_dir, exist_ok=True)

    multi_delta = 1.5  # 邻帧偏移秒数
    frame_files = []
    for i, ts in enumerate(key_ts):
        fname = f"ep{ep_num:02d}_f{i+1:02d}_{int(ts)}s.jpg"
        fpath = os.path.join(ep_dir, fname)
        ok = extract_frame(ep_num, ts, fpath)
        if not ok:
            print(f"    [WARN] 截帧失败 @ {ts}s")
            continue
        # 提取邻帧
        neighbor_paths = [fpath]
        for offset in [-multi_delta, multi_delta]:
            nt = max(0.5, ts + offset)
            nfname = f"ep{ep_num:02d}_f{i+1:02d}_{int(ts)}s_n{offset:+.0f}.jpg"
            nfpath = os.path.join(ep_dir, nfname)
            if extract_frame(ep_num, nt, nfpath):
                neighbor_paths.append(nfpath)
        frame_files.append((i + 1, ts, fpath, neighbor_paths))

    if not frame_files:
        print(f"  [EP{ep_num:02d}] [ERR] 无有效帧")
        return []

    # 去重
    kept = dedup_frames(ep_dir, ep_num)
    frame_files = [(idx, ts, fp, nps) for idx, ts, fp, nps in frame_files
                   if os.path.basename(fp) in kept]

    # Vision 分析（帧级并发）
    results = []
    prev_frame_id = None
    audio_segments = _ensure_episode_audio_features(ep_num, _resolve_episode_source(ep_num))

    def _analyze_single_frame(idx, ts, fpath, neighbor_paths):
        """多帧 Vision 分析（线程安全）。传主帧 + 邻帧列表。"""
        probe = frame_quality_probe(fpath)
        raw = call_vision_api(neighbor_paths if len(neighbor_paths) > 1 else fpath)
        return idx, ts, fpath, probe, raw

    sorted_frames = sorted(frame_files, key=lambda x: x[0])

    if _VISION_CONCURRENCY > 1 and len(sorted_frames) > 1:
        # 帧级并发：并行调用 API，结果按时间戳排序
        print(f"    Vision 并发={_VISION_CONCURRENCY}, {len(sorted_frames)}帧...")
        frame_results = {}
        with ThreadPoolExecutor(max_workers=_VISION_CONCURRENCY) as ex:
            futures = {ex.submit(_analyze_single_frame, idx, ts, fp, nps): (idx, ts)
                       for idx, ts, fp, nps in sorted_frames}
            for future in as_completed(futures):
                idx, ts, fpath, probe, raw = future.result()
                frame_results[idx] = (idx, ts, fpath, probe, raw)

        # 按 idx 顺序处理结果
        for idx in sorted(frame_results.keys()):
            idx, ts, fpath, probe, raw = frame_results[idx]
            if raw and not str(raw).startswith("API_ERROR"):
                v2 = extract_json_from_response(raw)
                if v2:
                    v2 = infer_quality_fields(v2, probe)
                    if audio_segments:
                        v2.update(summarize_audio_window(
                            audio_segments, ts,
                            window_seconds=_AUDIO_CFG.get("window_seconds", 2.5),
                            speech_peak_chars=_AUDIO_CFG.get("speech_peak_chars", 14),
                            energy_peak_threshold=_AUDIO_CFG.get("energy_peak_threshold", 0.72),
                        ))
                    line, fid = flatten_v2_for_output(v2, ep_num, ts, idx, prev_frame_id)
                    results.append(line)
                    prev_frame_id = fid
                    print(f"      [{idx}] OK event={v2.get('event','?')} q={v2.get('visual_quality','?')}")
                else:
                    print(f"      [{idx}] WARN JSON解析失败")
            else:
                print(f"      [{idx}] ERR API失败: {str(raw)[:60]}")
    else:
        # 串行模式
        for idx, ts, fpath, nps in sorted_frames:
            print(f"    [{idx}/{len(sorted_frames)}] Vision @ {ts}s...")
            probe = frame_quality_probe(fpath)
            raw = call_vision_api(nps if len(nps) > 1 else fpath)

            if raw and not str(raw).startswith("API_ERROR"):
                v2 = extract_json_from_response(raw)
                if v2:
                    v2 = infer_quality_fields(v2, probe)
                    if audio_segments:
                        v2.update(summarize_audio_window(
                            audio_segments, ts,
                            window_seconds=_AUDIO_CFG.get("window_seconds", 2.5),
                            speech_peak_chars=_AUDIO_CFG.get("speech_peak_chars", 14),
                            energy_peak_threshold=_AUDIO_CFG.get("energy_peak_threshold", 0.72),
                        ))
                    line, fid = flatten_v2_for_output(v2, ep_num, ts, idx, prev_frame_id)
                    results.append(line)
                    prev_frame_id = fid
                    print(f"      [OK] event={v2.get('event','?')} subtype={v2.get('event_subtype','?')} "
                          f"usable={v2.get('usable', True)} q={v2.get('visual_quality','?')}")
                else:
                    print(f"      [WARN] JSON解析失败: {raw[:80]}...")
            else:
                print(f"      [ERR] API失败: {raw}")

    print(f"  [EP{ep_num:02d}] [OK] {len(results)}/{len(frame_files)} 帧标记成功")
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='多帧采样 + V2标记')
    parser.add_argument('--dry-run', action='store_true', help='仅场景检测，不调API')
    parser.add_argument('--eps', type=str, default='', help='指定集数，逗号分隔 (如 1,7,52)')
    parser.add_argument('--scene-threshold', type=float, default=0.3, help='场景检测灵敏度 (默认0.3)')
    parser.add_argument('--workers', type=int, default=1, help='并行worker数 (API限流，建议1-2)')
    parser.add_argument('--resume', action='store_true', help='跳过已有标记的集数，断点续跑')
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 断点续跑: 加载已有标记的集数
    done_eps = set()
    if args.resume and os.path.exists(ANALYSIS_OUT):
        with open(ANALYSIS_OUT, 'r', encoding='utf-8') as f:
            for line in f:
                m = re.match(r'ep(\d+)_f', line.strip())
                if m:
                    done_eps.add(int(m.group(1)))
        if done_eps:
            print(f"  断点续跑: 已跳过 {len(done_eps)} 集\n")

    # 确定要处理的集数
    if args.eps:
        ep_list = [int(x.strip()) for x in args.eps.split(',')]
    else:
        if _project.get("episode_count"):
            ep_list = list(range(1, int(_project["episode_count"]) + 1))
        else:
            discovered = []
            if os.path.isdir(SOURCE_DIR):
                for name in os.listdir(SOURCE_DIR):
                    m = re.match(r'^(?:第)?(\d+)(?:集)?\.mp4$', name)
                    if m:
                        discovered.append(int(m.group(1)))
            ep_list = sorted(set(discovered))

    # 跳过已完成的
    if args.resume:
        ep_list = [ep for ep in ep_list if ep not in done_eps]
        if not ep_list:
            print("  所有集数已完成!")
            return

    t_start = time.time()
    print("=" * 60)
    print(f"  {PROJECT_NAME} 多帧采样 + V2标记")
    print(f"  集数: {len(ep_list)}集 | 场景阈值: {args.scene_threshold} | "
          f"Dry run: {args.dry_run} | Workers: {args.workers}")
    print("=" * 60)

    if args.dry_run:
        print("\n[Dry Run] 仅做场景检测，不调用Vision API\n")

    all_results = []
    success = 0
    fail = 0

    # 打开输出文件（增量写入）
    out_file = None
    if not args.dry_run:
        out_mode = 'a' if (args.resume and os.path.exists(ANALYSIS_OUT)) else 'w'
        out_file = open(ANALYSIS_OUT, out_mode, encoding='utf-8')

    try:
        if args.workers > 1:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = {ex.submit(process_episode, ep, args.scene_threshold, args.dry_run): ep
                          for ep in ep_list}
                for future in as_completed(futures):
                    ep = futures[future]
                    try:
                        res = future.result()
                        if args.dry_run:
                            all_results.append(res[0] if res else None)
                        else:
                            all_results.extend(res)
                            if out_file:
                                for line in res:
                                    out_file.write(line + '\n')
                                out_file.flush()
                        success += 1
                        print(f"  [进度] {success}/{len(ep_list)} 集完成", flush=True)
                    except Exception as e:
                        print(f"  [EP{ep:02d}] [ERR] 异常: {e}", flush=True)
                        fail += 1
        else:
            for ep in ep_list:
                try:
                    res = process_episode(ep, args.scene_threshold, args.dry_run)
                    if args.dry_run:
                        all_results.append(res[0] if res else None)
                    else:
                        all_results.extend(res)
                        if out_file:
                            for line in res:
                                out_file.write(line + '\n')
                            out_file.flush()
                    success += 1
                    print(f"  [进度] {success}/{len(ep_list)} 集完成", flush=True)
                except Exception as e:
                    print(f"  [EP{ep:02d}] [ERR] 异常: {e}", flush=True)
                    fail += 1
    finally:
        if out_file:
            out_file.close()

    # 输出结果
    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"  Dry Run 完成: {success}集检测成功, {fail}集失败")
        total_frames = sum(len(r['key_ts']) if r else 0 for r in all_results if r)
        print(f"  预估总帧数: ~{total_frames}")
        print(f"{'='*60}")
    else:
        elapsed = time.time() - t_start
        print(f"\n{'='*60}")
        print(f"  完成! {success}集处理, {fail}集失败")
        print(f"  总标记帧数: {len(all_results)}")
        print(f"  输出文件: {ANALYSIS_OUT}")
        print(f"  耗时: {elapsed:.0f}s")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
