"""
experiment_multiframe.py — 单帧 vs 三帧多图 对比实验

对 EP01 的 9 个关键帧，分别用单帧和多图两种方式调用 Vision API，
对比输出字段的分布差异。

用法:
  python experiment_multiframe.py
"""
import json, os, sys, time, base64, re, requests
from pathlib import Path
from collections import Counter

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))
from edit_utils import load_engine_config, load_project_config
from multi_frame_sample import (_resolve_episode_source, extract_frame,
    extract_json_from_response, _compress_image_for_api)

_cfg = load_engine_config()
_project = load_project_config(_cfg)
_vision_cfg = _cfg.get("vision", {}) if isinstance(_cfg.get("vision"), dict) else {}
API_KEY = _vision_cfg.get("api_key") or _cfg.get("dashscope_api_key", "")
API_BASE = _vision_cfg.get("base_url") or _cfg.get("dashscope_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
VISION_MODEL = _vision_cfg.get("model") or _cfg.get("vision_model", "qwen-vl-plus")
FFMPEG = _cfg.get("ffmpeg", "ffmpeg")
SOURCE_DIR = _project["media_dir"]
OUTPUT_DIR = _project["frames_dir"]
EXP_DIR = os.path.join(_project["work_dir"], "_experiment")
os.makedirs(EXP_DIR, exist_ok=True)

# ── 升级版 Prompt（5 维度打分 + 递进式负向扣分） ──
PROMPT_SINGLE = """请用JSON格式描述这张画面，只描述直接可见的内容，不要推测剧情。

返回格式（严格遵守）:
{
  "shot": "镜头类型(特写/近景/中景/全景/空镜)",
  "scene": "场景(室内/室外+具体地点；无法判断写未知)",
  "light": "光线(日/夜/黄昏+色调)",
  "chars": [
    {"id":"男子1/女子1等简称", "gender":"男/女/未知", "face":"正脸/侧脸/半面/模糊/背影/无人", "action":"只写可见动作", "emo":"平静/紧张/悲伤/愤怒/惊恐/痛苦/无"}
  ],
  "event": "主事件(冲突/对峙/日常/悲伤/打斗/跪地/威胁/其他)",
  "event_subtype": "细分事件(受伤/倒地/抓扯/持械/怒吼/哭泣/文字卡/空镜/奇幻爆发/无)",
  "event_conf": "可见/模糊",
  "emo": 1-5,
  "action_level": 1-5,
  "visual_quality": 1-5,
  "face_quality": "正脸/侧脸/半面/背影/模糊/无人",
  "dialogue_visible": true/false,
  "subtitle_text": "字幕文字，没有写空字符串",
  "usable": true/false,
  "reject_reason": "无/片头/片尾/纯文字/白屏/黑屏/模糊/空镜/低质量",
  "hint": "一句话描述可见内容，不推测。禁止使用似乎/可能/大概/仿佛/看起来像/似在",
  "props": "道具，分号分隔，没有写无",
  "mood": "画面整体氛围"
}

硬性规则:
1. 纯文字/白屏/黑屏/片头许可证/片尾标题/严重模糊/无人空镜 → usable=false
2. 看到口型只写"嘴唇微张/张嘴/闭嘴"，不推测说话内容
3. 主体模糊或事件不明确时 event_conf 必须写"模糊"
4. hint 禁止使用推测词"""

PROMPT_MULTI = """你是一位短剧宣发剪辑师。以下三张图来自同一段连续视频，按时间顺序排列（前→中→后）。请以"中间这张图"为主要评估对象，结合前后两帧的时序变化，给出以下 JSON 评估:

返回格式:
{
  "shot": "镜头类型",
  "event": "主事件",
  "event_subtype": "细分事件",
  "event_conf": "可见/模糊",
  "emo": 1-5,
  "action_level": 1-5,
  "visual_quality": 1-5,
  "face_quality": "正脸/侧脸/半面/背影/模糊/无人",
  "dialogue_visible": true/false,
  "subtitle_text": "字幕文字",
  "usable": true/false,
  "reject_reason": "无/片头/片尾/纯文字/白屏/黑屏/模糊/空镜/低质量",

  "action_continuity": true/false,
  "action_direction": "静止/增强/减弱/持续",
  "emotion_trend": "稳定/上升/下降/爆发",
  "camera_movement": "固定/摇镜/推拉/手持",

  "visual_impact": 1-5,
  "emotion_readability": 1-5,
  "edit_usability": 1-5,
  "action_clarity": 1-5,
  "promo_position": "开头钩子/中段堆叠/结尾悬念/不适合",

  "scene": "场景描述",
  "light": "光线描述",
  "chars": [{"id":"角色简称", "gender":"男/女/未知", "face":"正脸/侧脸/半面/模糊/背影/无人", "action":"可见动作", "emo":"情绪"}],
  "hint": "一句话描述中间帧的可见内容。禁止使用似乎/可能/大概/仿佛",
  "props": "道具",
  "mood": "氛围"
}

[评分标准]
- visual_impact(画面冲击力): 构图是否强、光线是否戏剧化、是否有视觉爆点。普通文戏≤3，身体对抗/刀剑/碎裂/大面积血迹才能4-5。
- emotion_readability(情绪可读性): 0.5秒内观众能否理解情绪。正脸+明确表情才能4-5。
- edit_usability(剪辑可用度): 这帧用在宣发里，观众是否会停下来看。
- action_clarity(动作清晰度): 动作是否明确可辨。

[负向扣分]
- 轻度瑕疵(台词重音瞬间闭眼/轻微侧脸) → edit_usability 减0.5-1分，但若情绪>=4则优先保留核心分
- 重度硬伤(失焦模糊/群演看镜头/演员尴尬过渡脸) → edit_usability 封顶2分，promo_position强制为不适合"""


def call_single(image_path):
    """单帧调用"""
    if not API_KEY: return None
    data_url = _compress_image_for_api(image_path)
    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": PROMPT_SINGLE},
        ]}],
        "stream": False, "max_tokens": 600,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    try:
        resp = requests.post(f"{API_BASE}/chat/completions", json=payload, headers=headers, timeout=45)
        if resp.status_code == 200:
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    except:
        pass
    return None


def call_multi(img_paths):
    """三帧多图调用"""
    if not API_KEY or len(img_paths) < 3: return None
    content = []
    for p in img_paths[:3]:
        content.append({"type": "image_url", "image_url": {"url": _compress_image_for_api(p)}})
    content.append({"type": "text", "text": PROMPT_MULTI})
    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "stream": False, "max_tokens": 800,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    try:
        resp = requests.post(f"{API_BASE}/chat/completions", json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    except:
        pass
    return None


def extract_neighbor_frames(ep_num, ts, delta=1.5):
    """提取 ts 前后 ±delta 的帧"""
    paths = []
    for offset in [-delta, 0, delta]:
        t = max(0.1, ts + offset)
        fpath = os.path.join(EXP_DIR, f"ep{ep_num:02d}_t{t:.1f}s.jpg")
        if not os.path.exists(fpath):
            extract_frame(ep_num, t, fpath)
        if os.path.exists(fpath) and os.path.getsize(fpath) > 1000:
            paths.append(fpath)
    return paths


def run_experiment(ep_num=1):
    """对指定集的全部关键帧做单帧 vs 多图对比"""
    # 读取已有的 V2 关键帧时间
    from edit_utils import parse_vision_line
    analysis_v3 = _project["analysis_v3"]
    frames = []
    if os.path.exists(analysis_v3):
        with open(analysis_v3, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(' ', 2)
                if len(parts) >= 3 and parts[0].startswith(f'ep{ep_num:02d}'):
                    # 时间戳从行的第二个字段取（如 "2s" → 2）
                    time_str = parts[1].replace('s', '')
                    ts = float(time_str) if time_str.replace('.','').isdigit() else 0
                    parsed = parse_vision_line(parts[2])
                    frames.append({
                        'frame_id': parts[0],
                        'ts': ts,
                        'old_emo': parsed.get('emotion', 3),
                        'old_action': parsed.get('action_level', 1),
                        'old_vq': parsed.get('visual_quality', 3),
                        'old_event': parsed.get('_v2', {}).get('event', '?'),
                        'old_subtype': parsed.get('event_subtype', '?'),
                        'old_hook': parsed.get('hook_value', 1),
                        'old_promo': parsed.get('promo_value', 1),
                    })

    if not frames:
        print(f"EP{ep_num:02d} 无已有V3数据，先跑采样")
        return

    print(f"EP{ep_num:02d} 对比实验: {len(frames)} 帧")
    print(f"{'='*70}")

    results = []
    for i, frm in enumerate(frames):
        ts = frm['ts']
        # 从已有截图目录找对应文件
        ep_dir = os.path.join(_project["frames_dir"], f"ep{ep_num:02d}")
        mid_path = None
        if os.path.isdir(ep_dir):
            # frame_id 如 ep01_f1 → 匹配 ep01_f01_*.jpg
            fid_short = frm['frame_id'].replace(f'ep{ep_num:02d}_f', '')
            idx_num = int(fid_short) if fid_short.isdigit() else 0
            pattern = f"ep{ep_num:02d}_f{idx_num:02d}_"
            matches = sorted([os.path.join(ep_dir, fn) for fn in os.listdir(ep_dir)
                            if fn.startswith(pattern) and fn.endswith('.jpg')])
            if matches:
                mid_path = matches[0]

        if not mid_path or not os.path.exists(mid_path):
            print(f"  [{i+1}/{len(frames)}] {frm['frame_id']} @ {ts}s → 截图不存在，跳过")
            continue

        # 提取邻帧
        neighbor_paths = extract_neighbor_frames(ep_num, ts)

        # 单帧
        raw_single = call_single(mid_path)
        v2_single = extract_json_from_response(raw_single) if raw_single else None

        # 多图
        multi_paths = []
        for offset in [-1.5, 0, 1.5]:
            t = max(0.1, ts + offset)
            fpath = os.path.join(EXP_DIR, f"ep{ep_num:02d}_t{t:.1f}s.jpg")
            if os.path.exists(fpath) and os.path.getsize(fpath) > 1000:
                multi_paths.append(fpath)
        raw_multi = call_multi(multi_paths) if len(multi_paths) >= 3 else None
        v2_multi = extract_json_from_response(raw_multi) if raw_multi else None

        # 对比
        s_emo = v2_single.get('emo', 0) if v2_single else 0
        m_emo = v2_multi.get('emo', 0) if v2_multi else 0
        s_action = v2_single.get('action_level', 0) if v2_single else 0
        m_action = v2_multi.get('action_level', 0) if v2_multi else 0
        s_vq = v2_single.get('visual_quality', 0) if v2_single else 0
        m_vq = v2_multi.get('visual_quality', 0) if v2_multi else 0
        s_vi = v2_single.get('visual_impact', 0) if v2_single else 0
        m_vi = v2_multi.get('visual_impact', 0) if v2_multi else 0
        m_eu = v2_multi.get('edit_usability', 0) if v2_multi else 0
        m_pp = v2_multi.get('promo_position', '?') if v2_multi else '?'
        m_cont = v2_multi.get('action_continuity', None) if v2_multi else None
        m_trend = v2_multi.get('emotion_trend', '?') if v2_multi else '?'
        m_dir = v2_multi.get('action_direction', '?') if v2_multi else '?'

        old_emo = frm['old_emo']
        old_action = frm['old_action']
        old_hook = frm['old_hook']

        print(f"  [{i+1}/{len(frames)}] {frm['frame_id']} @ {ts:.0f}s | 旧emo={old_emo} hook={old_hook}")
        print(f"    单帧: emo={s_emo} action={s_action} vq={s_vq} vi={s_vi}")
        print(f"    多图: emo={m_emo} action={m_action} vq={m_vq} vi={m_vi} edit={m_eu} pos={m_pp}")
        print(f"    多图: continuity={m_cont} trend={m_trend} action_dir={m_dir}")
        print()

        results.append({
            'frame_id': frm['frame_id'], 'ts': ts,
            'old_emo': old_emo, 'old_action': old_action, 'old_hook': old_hook,
            'single_emo': s_emo, 'single_action': s_action, 'single_vq': s_vq, 'single_vi': s_vi,
            'multi_emo': m_emo, 'multi_action': m_action, 'multi_vq': m_vq, 'multi_vi': m_vi,
            'multi_edit': m_eu, 'multi_pos': m_pp, 'multi_continuity': m_cont,
            'multi_trend': m_trend, 'multi_dir': m_dir,
        })
        time.sleep(0.3)  # 限流

    # ── 汇总 ──
    if not results:
        print("无对比数据")
        return

    n = len(results)
    emo_diff = [r['multi_emo'] - r['single_emo'] for r in results]
    action_diff = [r['multi_action'] - r['single_action'] for r in results]
    vq_diff = [r['multi_vq'] - r['single_vq'] for r in results]
    vi_diff = [r['multi_vi'] - r['single_vi'] for r in results if r['multi_vi'] > 0]

    # 多图新增字段统计
    pos_counter = Counter(r['multi_pos'] for r in results)
    trend_counter = Counter(r['multi_trend'] for r in results)
    cont_true = sum(1 for r in results if r['multi_continuity'] is True)
    eu_avg = sum(r['multi_edit'] for r in results if r['multi_edit'] > 0) / max(
        sum(1 for r in results if r['multi_edit'] > 0), 1)

    print(f"\n{'='*70}")
    print(f"  汇总对比 ({n} 帧)")
    print(f"{'='*70}")
    print(f"  emo: 单帧均值 {sum(r['single_emo'] for r in results)/n:.1f}  vs  多图均值 {sum(r['multi_emo'] for r in results)/n:.1f}  (差值 {sum(emo_diff)/n:+.1f})")
    print(f"  action: 单帧均值 {sum(r['single_action'] for r in results)/n:.1f}  vs  多图均值 {sum(r['multi_action'] for r in results)/n:.1f}  (差值 {sum(action_diff)/n:+.1f})")
    print(f"  visual_quality: 单帧均值 {sum(r['single_vq'] for r in results)/n:.1f}  vs  多图均值 {sum(r['multi_vq'] for r in results)/n:.1f}")
    if vi_diff:
        print(f"  visual_impact(多图新增): 均值 {sum(r['multi_vi'] for r in results)/n:.1f}")
    print(f"  edit_usability(多图新增): 均值 {eu_avg:.1f}")
    print(f"\n  多图 continuity: 有连续性={cont_true}/{n}")
    print(f"  多图 emotion_trend: {dict(trend_counter)}")
    print(f"  多图 promo_position: {dict(pos_counter)}")
    print(f"\n  emo 变化分布: 上升{sum(1 for d in emo_diff if d > 0)} / 不变{sum(1 for d in emo_diff if d == 0)} / 下降{sum(1 for d in emo_diff if d < 0)}")
    print(f"  action 变化分布: 上升{sum(1 for d in action_diff if d > 0)} / 不变{sum(1 for d in action_diff if d == 0)} / 下降{sum(1 for d in action_diff if d < 0)}")

    # 保存详细结果
    out_path = os.path.join(EXP_DIR, "experiment_results.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  详细结果: {out_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='单帧 vs 多图对比实验')
    parser.add_argument('--ep', type=int, default=1, help='集数')
    args = parser.parse_args()
    run_experiment(args.ep)
