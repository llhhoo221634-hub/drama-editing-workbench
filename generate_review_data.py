"""generate_review_data.py — 生成审片预览片段 + 元数据 JS"""
import json, subprocess, os, sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))
from edit_utils import parse_vision_line, load_project_config
from multi_frame_sample import _resolve_episode_source

_project = load_project_config()
FFMPEG = _project.get("ffmpeg", "") or os.environ.get("FFMPEG", "ffmpeg")
if not os.path.exists(FFMPEG):
    # fallback
    for p in [r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"]:
        if os.path.exists(p):
            FFMPEG = p
            break

SOURCE = _project["media_dir"]
CLIP_OUT = os.path.join(_project["work_dir"], "_review_clips")
STATIC_DIR = os.path.join(SKILL_DIR, "static")
os.makedirs(CLIP_OUT, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
TEMPLATE = _project["episode_name_template"]

# ── 加载 V3 数据 ──
with open(_project["analysis_v3"], 'r', encoding='utf-8') as f:
    frames = []
    for line in f:
        parts = line.strip().split(' ', 2)
        if len(parts) < 3: continue
        ep_match = __import__('re').match(r'ep(\d+)_f', parts[0])
        ep = int(ep_match.group(1)) if ep_match else 0
        ts = float(parts[1].replace('s', ''))
        parsed = parse_vision_line(parts[2])
        if not parsed.get('usable') or parsed.get('reject_reason','无') != '无':
            continue
        frames.append({
            'frame_id': parts[0],
            'ep': ep,
            'ts': ts,
            'hook': parsed.get('hook_value', 1),
            'promo': parsed.get('promo_value', 1),
            'emo': parsed.get('emotion', 3),
            'action': parsed.get('action_level', 1),
            'event': parsed.get('_v2', {}).get('event', '?'),
            'subtype': parsed.get('event_subtype', ''),
            'vq': parsed.get('visual_quality', 3),
            'face': parsed.get('face_quality', '?'),
            'ad': parsed.get('action_direction', '?'),
            'et': parsed.get('emotion_trend', '?'),
            'cut_role': parsed.get('cut_role', '?'),
            'story_stage': parsed.get('story_stage', ''),
            'hint': (parsed.get('hint', '') or '')[:200],
            'subtitles': (parsed.get('subtitle_text', '') or '')[:100],
        })

# 叙事阶段多样性排序：每阶段至少取 top-N
from collections import Counter
stage_buckets = {}
for frm in frames:
    st = frm.get('story_stage', '')
    if st not in stage_buckets:
        stage_buckets[st] = []
    stage_buckets[st].append(frm)

# 每阶段内按 hook 排序，阶段间轮询取
for st in stage_buckets:
    stage_buckets[st].sort(key=lambda x: -x['hook'])

top50 = []
stage_ptrs = {st: 0 for st in stage_buckets}
while len(top50) < 50:
    added = False
    for st in sorted(stage_buckets.keys()):
        ptr = stage_ptrs[st]
        if ptr < len(stage_buckets[st]):
            top50.append(stage_buckets[st][ptr])
            stage_ptrs[st] = ptr + 1
            added = True
        if len(top50) >= 50:
            break
    if not added:
        break

# ── 生成 3 秒预览片段（ts-1.5 ~ ts+1.5，聚焦动作爆发点）──
print(f"Generating {len(top50)} preview clips (3s each)...")
count = 0
for i, frm in enumerate(top50):
    ep = frm['ep']
    ts = frm['ts']
    src = _resolve_episode_source(ep)
    if not src:
        continue
    out_name = f"ep{ep:02d}_{int(ts)}s.mp4"
    out_mp4 = os.path.join(CLIP_OUT, out_name)
    frm['clip_file'] = out_name

    if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 1000:
        count += 1
        continue

    start = max(0, ts - 1.5)
    r = subprocess.run([
        FFMPEG, '-y', '-ss', str(start), '-t', '3', '-i', src,
        '-vf', 'scale=360:640,fps=15,setpts=PTS-STARTPTS',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '64k', '-af', 'volume=1.5',
        out_mp4
    ], capture_output=True, timeout=15)
    if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 1000:
        count += 1
    if (i+1) % 10 == 0:
        print(f"  {i+1}/{len(top50)}...")

print(f"  Done: {count}/{len(top50)} clips")

# ── 生成混剪预览（Top 15 帧各切 2-3s 拼接）──
print(f"\nGenerating montage preview from top 15 clips...")
montage_out = os.path.join(CLIP_OUT, "_montage_preview.mp4")
top15 = top50[:15]
temp_dir = os.path.join(CLIP_OUT, "_montage_parts")
os.makedirs(temp_dir, exist_ok=True)

concat_list = os.path.join(temp_dir, "concat.txt")
with open(concat_list, 'w', encoding='utf-8') as cl:
    for i, frm in enumerate(top15):
        ep = frm['ep']
        ts = frm['ts']
        src = _resolve_episode_source(ep)
        if not src:
            continue
        part_out = os.path.join(temp_dir, f"m_{i:02d}.mp4")
        dur = 2.5  # 每段 2.5s
        start = max(0, ts - 0.8)
        subprocess.run([
            FFMPEG, '-y', '-ss', str(start), '-t', str(dur), '-i', src,
            '-vf', 'scale=360:640,fps=15,setpts=PTS-STARTPTS',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
            '-c:a', 'aac', '-b:a', '64k', '-af', 'volume=1.5',
            part_out
        ], capture_output=True, timeout=15)
        if os.path.exists(part_out) and os.path.getsize(part_out) > 1000:
            cl.write(f"file '{part_out}'\n")

subprocess.run([
    FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', concat_list,
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
    '-c:a', 'aac', '-b:a', '64k',
    montage_out
], capture_output=True, timeout=30)

import shutil
shutil.rmtree(temp_dir, ignore_errors=True)
if os.path.exists(montage_out):
    print(f"  Montage: {montage_out} ({os.path.getsize(montage_out)//1024}KB)")
else:
    print(f"  Montage generation failed")

# ── 生成元数据 JS ──
js_path = os.path.join(STATIC_DIR, "review_data.js")
with open(js_path, 'w', encoding='utf-8') as f:
    f.write("// Auto-generated review data — DO NOT EDIT\n")
    f.write("const REVIEW_CLIPS = ")
    json.dump(top50, f, ensure_ascii=False, indent=2)
    f.write(";\n")
    f.write(f"const CLIP_BASE_URL = '/clips/';\n")

print(f"Metadata: {js_path}")
print(f"Clips: {CLIP_OUT}")
print(f"\nNext: python review_server.py")
print(f"Then: http://localhost:8888")
