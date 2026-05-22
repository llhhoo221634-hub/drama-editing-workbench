"""
calibrate_v3.py — V3 评分盲标数据集生成 & 对比分析

用法:
  python calibrate_v3.py generate    → 从 V3 随机抽 30 帧，输出盲标 CSV
  python calibrate_v3.py analyze     → 读回标注后的 CSV，计算混淆矩阵
"""
import json, os, sys, random, csv
from pathlib import Path

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))
from edit_utils import parse_vision_line, load_project_config

_project = load_project_config()
ANALYSIS_V3 = _project["analysis_v3"]
OUT_DIR = _project["work_dir"]


def load_usable_frames():
    frames = []
    with open(ANALYSIS_V3, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            parts = line.split(' ', 2)
            if len(parts) < 3: continue
            parsed = parse_vision_line(parts[2])
            if parsed.get('usable') and parsed.get('reject_reason','无') == '无':
                # 附加原始 JSON 中的 V3 字段
                frames.append({
                    **parsed,
                    'frame_id': parts[0],
                    'json_raw': parts[2],
                })
    return frames


def cmd_generate(n=30):
    frames = load_usable_frames()
    print(f"可用帧总数: {len(frames)}")
    if len(frames) < n:
        n = len(frames)
    sample = random.sample(frames, n)

    csv_path = os.path.join(OUT_DIR, "v3_calibration_blind.csv")
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "label(1=不可用/2=可用/3=顶级)",
            "ep", "time(s)", "event", "subtype", "shot",
            "emo", "visual_quality", "face_quality",
            "audio_energy", "dialogue_anchor",
            "cut_role", "best_cut",
            "desc"
        ])
        for c in sample:
            desc = (c.get('desc_clean', '') or '')[:100]
            writer.writerow([
                "",  # 留空待标
                c.get('ep', '?'),
                c.get('time', 0),
                c.get('_v2', {}).get('event', '?'),
                c.get('event_subtype', ''),
                c.get('_v2', {}).get('shot', '?'),
                c.get('emotion', 3),
                c.get('visual_quality', 3),
                c.get('face_quality', '?'),
                c.get('audio_energy', 1),
                c.get('dialogue_anchor', 'none'),
                c.get('cut_role', '?'),
                c.get('best_cut', '?'),
                desc,
            ])

    # 同时保存完整标注参考（带 V3 分数，标完后对照用）
    ref_path = os.path.join(OUT_DIR, "v3_calibration_reference.json")
    ref_data = []
    for c in sample:
        ref_data.append({
            "frame_id": c['frame_id'],
            "ep": c.get('ep'),
            "time": c.get('time'),
            "hook_value": c.get('hook_value', 1),
            "promo_value": c.get('promo_value', 1),
            "emotion_value": c.get('emotion_value', 1),
            "action_value": c.get('action_value', 1),
            "visual_value": c.get('visual_value', 1),
            "cut_role": c.get('cut_role'),
            "best_cut": c.get('best_cut'),
            "event": c.get('_v2', {}).get('event', ''),
            "desc": (c.get('desc_clean', '') or '')[:100],
        })
    with open(ref_path, 'w', encoding='utf-8') as f:
        json.dump(ref_data, f, ensure_ascii=False, indent=2)

    print(f"盲标CSV: {csv_path}")
    print(f"参考答案: {ref_path}  (标完后再看)")
    print(f"随机抽取 {n} 帧，覆盖集数: {sorted(set(c.get('ep') for c in sample))}")


def cmd_analyze():
    csv_path = os.path.join(OUT_DIR, "v3_calibration_blind.csv")
    ref_path = os.path.join(OUT_DIR, "v3_calibration_reference.json")

    if not os.path.exists(csv_path):
        print(f"[ERR] 盲标CSV不存在: {csv_path}，请先运行 generate")
        return
    if not os.path.exists(ref_path):
        print(f"[ERR] 参考答案不存在: {ref_path}")

    with open(ref_path, 'r', encoding='utf-8') as f:
        refs = json.load(f)

    labels = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            val = str(row.get('label(1=不可用/2=可用/3=顶级)', '')).strip()
            if val:
                labels.append(int(val))
            else:
                labels.append(None)

    if len(labels) != len(refs):
        print(f"[WARN] 标注数({len(labels)})与参考数({len(refs)})不匹配")

    labeled = [(lab, ref) for lab, ref in zip(labels, refs) if lab is not None]
    print(f"已标注: {len(labeled)}/{len(refs)} 帧\n")

    # 按 hook_value 分组统计
    for hv_thresh, label_name in [(4, "hook_value>=4"), (3, "hook_value>=3"), (1, "全部")]:
        group = [(l, r) for l, r in labeled if r['hook_value'] >= hv_thresh]
        if not group:
            continue
        top = sum(1 for l, _ in group if l == 3)
        usable = sum(1 for l, _ in group if l >= 2)
        unusable = sum(1 for l, _ in group if l == 1)
        n = len(group)
        print(f"  [{label_name}] n={n}")
        print(f"    顶级(label=3): {top}/{n} ({top/n*100:.0f}%)")
        print(f"    可用(label>=2): {usable}/{n} ({usable/n*100:.0f}%)")
        print(f"    不可用(label=1): {unusable}/{n} ({unusable/n*100:.0f}%)")
        if n > 0 and unusable > 0:
            print(f"    ⚠ 假阳性率(系统认为可用但人标不可用): {unusable/n*100:.0f}%")
        print()

    # 混淆矩阵 (hook_value>=3 vs label)
    print("  混淆矩阵 (hook_value>=3 vs 人工标注):")
    hv3 = [(l, r) for l, r in labeled if r['hook_value'] >= 3]
    tp = sum(1 for l, _ in hv3 if l >= 2)
    fp = sum(1 for l, _ in hv3 if l == 1)
    pre = tp / len(hv3) * 100 if hv3 else 0
    print(f"    精确率(Precision): {tp}/{len(hv3)} = {pre:.0f}%")

    low = [(l, r) for l, r in labeled if r['hook_value'] < 3]
    fn = sum(1 for l, _ in low if l >= 3)
    tn = sum(1 for l, _ in low if l <= 2)
    print(f"    低分帧中的顶级漏报: {fn}/{len(low) if low else 1}")
    print(f"    低分帧正确排除: {tn}/{len(low) if low else 1}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='V3 评分盲标校准')
    parser.add_argument('cmd', choices=['generate', 'analyze'],
                       help='generate: 生成盲标CSV; analyze: 分析标注结果')
    parser.add_argument('-n', type=int, default=30, help='抽样数量 (默认30)')
    args = parser.parse_args()

    if args.cmd == 'generate':
        cmd_generate(args.n)
    else:
        cmd_analyze()
