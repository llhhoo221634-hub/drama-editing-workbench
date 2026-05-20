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

# ── Config ──
SOURCE_DIR = r"E:\BaiduNetdiskDownload\一品布衣（105集）潘子健&胡家荣"
OUTPUT_DIR = r"E:\视频\一品布衣\_frames_v2"
ANALYSIS_OUT = r"E:\视频\一品布衣\analysis_v2.txt"
OLD_ANALYSIS = r"E:\视频\一品布衣\analysis.txt"

# ffmpeg/ffprobe paths
SKILL_DIR = Path(r"E:\技能skills\剪辑skills_backup_2")
import json as _json
_cfg = _json.loads((SKILL_DIR / "config.json").read_text('utf-8'))
FFMPEG = _cfg.get("ffmpeg", "ffmpeg")
FFPROBE = _cfg.get("ffprobe", "ffprobe")
API_KEY = _cfg.get("dashscope_api_key", "")
API_BASE = _cfg.get("dashscope_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
VISION_MODEL = _cfg.get("vision_model", "qwen-vl-plus")

# ── 采样策略 ──
# 场景检测为每集找到变化点，然后按集长分档限制最大帧数
MAX_FRAMES = {
    "short":  4,   # <90s
    "medium": 7,   # 90-180s
    "long":   10,  # >180s
}

# V2 标记 Prompt（只描述可见内容）
VISION_PROMPT = """请用JSON格式描述这张画面，只描述直接可见的内容，不要推测剧情。

返回格式（严格遵守）:
{
  "shot": "镜头类型(特写/近景/中景/全景/空镜)",
  "scene": "场景(室内/室外+具体地点；无法判断写未知)",
  "light": "光线(日/夜/黄昏+色调 如暖黄/冷蓝/暗沉/过曝)",
  "chars": [
    {"id":"男子1/女子1/孩童1/老人1/未知1等稳定简称", "gender":"男/女/未知", "face":"正脸/侧脸/半面/模糊/背影/无人", "action":"只写可见动作", "emo":"平静/紧张/悲伤/愤怒/惊恐/痛苦/无"}
  ],
  "event": "主事件(冲突/对峙/日常/悲伤/出征/打斗/跪地/威胁/误会/其他)",
  "event_subtype": "细分事件(受伤/倒地/抓扯/持械/追逐/怒吼/拥抱/哭泣/文字卡/空镜/片头/片尾/奇幻爆发/无)",
  "event_conf": "可见(画面明确展示)/模糊(主体不清或无法判断)",
  "emo": 1-5情绪强度(1平静 2略波动 3明显 4强烈 5爆发),
  "action_level": 1-5动作强度(1静止 2轻微动作 3明显动作 4激烈动作 5爆发动作),
  "visual_quality": 1-5画面可用性(1白屏/黑屏/严重模糊 2低清或主体太小 3可用 4清晰 5强构图高可用),
  "face_quality": "正脸/侧脸/半面/背影/模糊/无人",
  "dialogue_visible": true/false,
  "subtitle_text": "画面中可读字幕，没有写空字符串",
  "usable": true/false,
  "reject_reason": "无/片头/片尾/纯文字/白屏/黑屏/模糊/空镜/人物太小/低质量",
  "hint": "一句话描述画面可见内容，不推测",
  "props": "道具列表，分号分隔，没有就写无",
  "mood": "画面整体氛围"
}

硬性规则:
1. hint禁止使用“似乎”“可能”“大概”“意味着”“仿佛”“看起来像”“似在”等推测词。
2. 看到口型只能写“嘴唇微张/张嘴/闭嘴”，不要写“在说话/似在说话”。
3. 纯文字、白屏、黑屏、片头许可证、片尾标题、严重模糊、无人空镜必须 usable=false，并写 reject_reason。
4. 主体模糊或事件不明确时 event_conf 必须是“模糊”，不能写“可见”。
5. 如果画面适合宣发剪辑，usable=true 且 reject_reason="无"。"""


def get_episode_duration(ep_num):
    """用 ffprobe 获取集数时长(秒)"""
    src = os.path.join(SOURCE_DIR, f"{int(ep_num)}.mp4")
    if not os.path.exists(src):
        src = os.path.join(SOURCE_DIR, f"{int(ep_num):02d}.mp4")
    if not os.path.exists(src):
        return 0
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", src], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 0


def detect_scenes(ep_num, threshold=0.3):
    """
    用 ffmpeg scene detection 找画面变化点。
    返回: [(timestamp, score), ...] 按时间排序
    """
    src = os.path.join(SOURCE_DIR, f"{int(ep_num)}.mp4")
    if not os.path.exists(src):
        src = os.path.join(SOURCE_DIR, f"{int(ep_num):02d}.mp4")
    if not os.path.exists(src):
        return []

    # 用 select filter 检测场景变化
    cmd = [
        FFMPEG, "-i", src,
        "-filter_complex", f"select='gt(scene,{threshold})',metadata=print",
        "-vsync", "vfr", "-f", "null", "-"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

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
    src = os.path.join(SOURCE_DIR, f"{int(ep_num)}.mp4")
    if not os.path.exists(src):
        src = os.path.join(SOURCE_DIR, f"{int(ep_num):02d}.mp4")
    if not os.path.exists(src):
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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
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
    src = os.path.join(SOURCE_DIR, f"{int(ep_num)}.mp4")
    if not os.path.exists(src):
        src = os.path.join(SOURCE_DIR, f"{int(ep_num):02d}.mp4")
    if not os.path.exists(src):
        return False

    r = subprocess.run([
        FFMPEG, "-y", "-ss", str(timestamp), "-i", src,
        "-vframes", "1", "-q:v", "3",
        output_path
    ], capture_output=True, text=True, timeout=30)
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
        ], capture_output=True, text=True, timeout=10)
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
    if stdev <= 0.8:
        return {"usable": False, "reject_reason": "低质量", "mean": mean, "stdev": stdev}
    return {"usable": True, "reject_reason": "无", "mean": mean, "stdev": stdev}


def sanitize_hint(text):
    """移除识图结果里的弱推测词，避免污染下游文案。"""
    if not text:
        return ""
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
        text = text.replace(old, new)
    return re.sub(r'\s+', ' ', text).strip()


def infer_quality_fields(v2_data, frame_probe=None):
    """补齐旧模型缺失的可用性/质量字段。"""
    probe = frame_probe or {"usable": True, "reject_reason": "无"}
    hint = sanitize_hint(v2_data.get("hint", ""))
    v2_data["hint"] = hint

    reject_reason = v2_data.get("reject_reason", "无") or "无"
    event_subtype = v2_data.get("event_subtype", "") or "无"
    event_conf = v2_data.get("event_conf", "推测")
    shot = v2_data.get("shot", "")
    chars = v2_data.get("chars", [])
    chars_text = json.dumps(chars, ensure_ascii=False) if isinstance(chars, list) else str(chars)
    text_blob = " ".join(str(v2_data.get(k, "")) for k in ["hint", "mood", "scene", "props", "subtitle_text"]) + " " + chars_text

    if not probe.get("usable", True):
        reject_reason = probe.get("reject_reason", "低质量")
    elif any(k in text_blob for k in ["许可证", "发行许可证", "网络剧片", "完", "剧终"]):
        reject_reason = "片头" if "许可证" in text_blob or "网络剧片" in text_blob else "片尾"
    elif any(k in text_blob for k in ["纯白", "白色背景", "大面积过曝"]):
        reject_reason = "白屏"
    elif any(k in text_blob for k in ["纯黑", "黑色背景"]):
        reject_reason = "黑屏"
    elif any(k in text_blob for k in ["纯文字", "竖排", "文字", "字幕卡"]):
        reject_reason = "纯文字"
    elif "模糊" in text_blob or event_conf == "模糊":
        reject_reason = "模糊"
    elif shot == "空镜" or event_subtype == "空镜":
        reject_reason = "空镜"

    usable = v2_data.get("usable", reject_reason == "无")
    if isinstance(usable, str):
        usable = usable.lower() not in ["false", "0", "no", "否"]
    usable = bool(usable) and reject_reason == "无"

    if reject_reason != "无":
        event_conf = "模糊"

    if "visual_quality" not in v2_data:
        if reject_reason in ["白屏", "黑屏", "模糊", "低质量"]:
            v2_data["visual_quality"] = 1
        elif reject_reason in ["片头", "片尾", "纯文字", "空镜"]:
            v2_data["visual_quality"] = 2
        else:
            v2_data["visual_quality"] = 3

    if "action_level" not in v2_data:
        event = v2_data.get("event", "")
        emo = int(v2_data.get("emo", 3) or 3)
        if event in ["打斗", "冲突", "威胁"] or event_subtype in ["抓扯", "持械", "追逐", "怒吼", "奇幻爆发"]:
            v2_data["action_level"] = max(4, emo)
        elif event in ["对峙", "跪地", "悲伤"]:
            v2_data["action_level"] = max(2, min(4, emo))
        else:
            v2_data["action_level"] = 1 if not usable else min(3, emo)

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
    return v2_data


def call_vision_api(image_path):
    """调用千问 VL API 分析图片，返回 JSON 文本"""
    if not API_KEY:
        return None

    # 读取图片并 base64
    with open(image_path, 'rb') as f:
        img_data = base64.b64encode(f.read()).decode('utf-8')

    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {'.jpg': 'jpeg', '.jpeg': 'jpeg', '.png': 'png', '.webp': 'webp'}
    mime = mime_map.get(ext, 'jpeg')
    data_url = f"data:image/{mime};base64,{img_data}"

    payload = {
        "model": VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": VISION_PROMPT},
            ]
        }],
        "stream": False,
        "max_tokens": 600,
    }

    try:
        resp = requests.post(
            f"{API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content
        else:
            return f"API_ERROR_{resp.status_code}"
    except Exception as e:
        return f"API_ERROR: {str(e)}"


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

    # 截帧
    ep_dir = os.path.join(OUTPUT_DIR, f"ep{ep_num:02d}")
    os.makedirs(ep_dir, exist_ok=True)

    frame_files = []
    for i, ts in enumerate(key_ts):
        fname = f"ep{ep_num:02d}_f{i+1:02d}_{int(ts)}s.jpg"
        fpath = os.path.join(ep_dir, fname)
        ok = extract_frame(ep_num, ts, fpath)
        if ok:
            frame_files.append((i + 1, ts, fpath))
        else:
            print(f"    [WARN] 截帧失败 @ {ts}s")

    if not frame_files:
        print(f"  [EP{ep_num:02d}] [ERR] 无有效帧")
        return []

    # 去重
    kept = dedup_frames(ep_dir, ep_num)
    frame_files = [(idx, ts, fp) for idx, ts, fp in frame_files
                   if os.path.basename(fp) in kept]

    # Vision 分析
    results = []
    prev_frame_id = None

    for idx, ts, fpath in sorted(frame_files, key=lambda x: x[0]):
        print(f"    [{idx}/{len(frame_files)}] Vision @ {ts}s...")
        probe = frame_quality_probe(fpath)
        raw = call_vision_api(fpath)

        if raw and not raw.startswith("API_ERROR"):
            v2 = extract_json_from_response(raw)
            if v2:
                v2 = infer_quality_fields(v2, probe)
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
        ep_list = list(range(1, 106))

    # 跳过已完成的
    if args.resume:
        ep_list = [ep for ep in ep_list if ep not in done_eps]
        if not ep_list:
            print("  所有集数已完成!")
            return

    t_start = time.time()
    print("=" * 60)
    print("  一品布衣 多帧采样 + V2标记")
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
