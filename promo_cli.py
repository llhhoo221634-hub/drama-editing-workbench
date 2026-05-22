"""
promo_cli.py — 分子级拆解宣发 CLI 入口
"""
import sys, os, json, time, subprocess, re, csv
from pathlib import Path

# ── Path setup ──
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

from config import get_engine_config, get_project_config

from edit_utils import (
    parse_vision_line,
    parallel_cut_clips,
    check_audio_quality,
    fact_check_selection,
    episode_filename,
    write_json,
)
from genre_engine import (_genre_detect_two_pass, multi_pass_rank,
                          EDIT_DIRECTIONS,
                          recommend_directions, apply_direction_weights,
                          prefilter_for_direction, enforce_narrative_diversity,
                          tag_narrative_function)

from molecule_types import MOLECULE_TYPES, FUSION_WEIGHTS, TRANSITION_PROFILE_MAP, clip_event_text, molecular_score, is_high_conflict_clip
from selection_scorer import (molecular_fusion_score,
                              narrative_boost, golden_quote_boost,
                              load_story_profile, load_golden_quotes, load_character_relationships,
                              _parse_ep_range, _load_full_profile, load_analysis)
from selection_constraints import (_molecular_filter_pass, molecular_filter, _is_legacy_data,
                                   deduplicate_clips, apply_hard_constraints)
from narrative_sequencer import narrative_order
from molecular_assembler import (cut_molecular_clips, assemble_molecular, build_molecule_timeline,
                                  write_timeline_file, molecular_qa_checks, write_qa_report)

# ── Config ──
_cfg = get_engine_config()
_project = get_project_config()
_render_cfg = (_cfg.get("render") or {}) if isinstance(_cfg.get("render"), dict) else {}
SOURCE_DIR = _project["media_dir"]
ANALYSIS_FILE = _project["analysis_v3"]
ANALYSIS_FALLBACK = _project["analysis_fallback"]
OUTPUT_DIR = _project["molecular_dir"]
BGM_FILE = _project["bgm"]
PROJECT_NAME = _project["project_name"]
EPISODE_NAME_TEMPLATE = _project["episode_name_template"]
FFMPEG = _cfg.get("ffmpeg", "ffmpeg")
FFPROBE = _cfg.get("ffprobe", "ffprobe")


def name_func(ep):
    return episode_filename(ep, EPISODE_NAME_TEMPLATE)


def molecule_output_path(mol_type):
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


def molecular_rank(pool, mol_type, top_n=10):
    """分子类型特定的排序 + 叙事多样性"""
    mdef = MOLECULE_TYPES[mol_type]

    # Type-weighted fusion score as primary sort key
    ranked = sorted(pool, key=lambda c: -molecular_fusion_score(c, mol_type))

    # 简单叙事多样性: 按 narrative_beats 比例取
    beat_ratio = mdef.get('narrative_beats', {})
    n = min(top_n, len(ranked))
    target_beats = {}
    for beat, ratio in beat_ratio.items():
        target_beats[beat] = max(1, round(n * ratio))

    # 打标签
    for c in ranked:
        c['_nf'] = tag_narrative_function(c)

    # 按节拍选取
    by_beat = {}
    for c in ranked:
        nf = c.get('_nf', 'rising')
        by_beat.setdefault(nf, []).append(c)

    selected = []
    beat_used = {}
    for beat in ['hook', 'setup', 'inciting', 'rising', 'climax', 'transition']:
        need = target_beats.get(beat, 0)
        candidates = by_beat.get(beat, [])
        for c in candidates:
            if need <= 0:
                break
            if c not in selected:
                selected.append(c)
                beat_used[beat] = beat_used.get(beat, 0) + 1
                need -= 1

    # 填满
    if len(selected) < n:
        for c in ranked:
            if c not in selected:
                selected.append(c)
            if len(selected) >= n:
                break

    if mol_type in ['hook_clash', 'quote_rhythm', 'suspense_hook']:
        hook = next((c for c in ranked if is_high_conflict_clip(c)), None)
        if hook:
            selected = [hook] + [c for c in selected if c is not hook]

    spread = deduplicate_clips(selected, window_seconds=5)
    if len(spread) < n:
        # 补充被去重掉的候选
        remaining = [c for c in selected if c not in spread]
        remaining_dedup = deduplicate_clips(remaining, window_seconds=3)
        for c in remaining_dedup:
            if c not in spread:
                spread.append(c)
            if len(spread) >= n:
                break

    return spread[:n]


def process_molecule(clips, mol_type, dry_run=False, export_csv=False, review_selections=None, xfade_type='fade'):
    """处理单条分子宣发。
    review_selections: 可选 dict，key 为 (ep, time)，value 为 keep (bool)。
                       传入时跳过自动选片，直接使用人工审核后的片段列表。"""

    mdef = MOLECULE_TYPES[mol_type]
    print(f"\n{'='*60}")
    print(f"  分子类型: {mdef['name']} ({mol_type})")
    print(f"  目标: {mdef['target_dur']}s | {mdef['min_clips']}-{mdef['max_clips']}片段")
    print(f"  规则: {mdef['hook_rule']}")
    print(f"{'='*60}")

    # ── 人工审核模式：从 CSV 读取已确认片段 ──
    if review_selections is not None:
        kept = [c for c in clips if review_selections.get(
            (int(c.get('ep', '0') or 0), int(c.get('time', 0))), False
        )]
        # 补充 clips 中未出现在 review_selections 但在同分子类型中评分高的片段
        if len(kept) < mdef['max_clips']:
            pool = molecular_filter(clips, mol_type)
            for c in molecular_rank(pool, mol_type, top_n=mdef['max_clips'] * 3):
                key = (int(c.get('ep', '0') or 0), int(c.get('time', 0)))
                if not review_selections.get(key, True):  # 明确被标记为 N 的跳过
                    continue
                if key not in {(int(k.get('ep', '0') or 0), int(k.get('time', 0))) for k in kept}:
                    kept.append(c)
                if len(kept) >= mdef['max_clips']:
                    break
        selected = kept[:mdef['max_clips']]
        print(f"  [人工审核] 确认 {len(selected)} 片段 (从审核表读取)")
    else:
        # 预筛选
        pool = molecular_filter(clips, mol_type)
        print(f"  预筛选: {len(clips)} → {len(pool)} 片段")

        if len(pool) < mdef['min_clips']:
            print(f"  [ERR] 候选片段不足")
            return None

        # 排序 + 叙事多样性
        ranked = molecular_rank(pool, mol_type, top_n=mdef['max_clips'])
        selected = apply_hard_constraints(ranked, target_count=mdef['min_clips'],
                                          max_per_ep=4, max_same_shot=2)
        selected = narrative_order(selected)
        # 动态低水位：候选不够时自动降低 min_clips
        effective_min = min(mdef['min_clips'], max(8, len(ranked) // 2))
        if len(selected) < effective_min:
            # V2 only fallback: V1 数据 event 为 "?" 且有极短描述
            v2_only = [c for c in ranked if (c.get('aesthetic_score', 0) or 0) >= 5
                        and (c.get('_v2', {}).get('event', '') or '') != '?'
                        and len(c.get('hint', '') or c.get('desc_clean', '') or c.get('desc', '') or '') > 15]
            for fallback_ep, fallback_shot in [(6, 3), (10, 5)]:
                relaxed = apply_hard_constraints(v2_only, max_per_ep=fallback_ep, max_same_shot=fallback_shot)
                if len(relaxed) >= 6:
                    selected = relaxed[:mdef['max_clips']]
                    break
            else:
                selected = v2_only[:mdef['max_clips']]
    print(f"\n  选中 {len(selected)} 片段:")
    for i, c in enumerate(selected):
        ep = c.get('ep', '?')
        t = c.get('time', 0)
        emo = c.get('emotion', 3)
        nf = c.get('_nf', '?')
        hint = c.get('desc_clean', c.get('desc', ''))[:50]
        event = c.get('_v2', {}).get('event', '?')
        print(f"  {i+1}. EP{ep} {t}s emo={emo} event={event} func={nf} "
              f"q={c.get('visual_quality', '?')} face={c.get('face_quality', '?')} "
              f"score={molecular_score(c, mol_type):.0f} \"{hint}\"")

    ep_nums = sorted(set(int(c.get('ep', '0') or 0) for c in selected))
    if ep_nums:
        print(f"  覆盖: EP{min(ep_nums)}-EP{max(ep_nums)} ({len(ep_nums)}集)")

    # ── CSV 导出 ──
    if export_csv:
        csv_path = os.path.join(OUTPUT_DIR, f"review_{mol_type}.csv")
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as cf:
            writer = csv.writer(cf)
            writer.writerow(["keep(Y/N)", "ep", "time(s)", "suggested_dur(s)",
                            "cut_role", "best_cut", "event", "subtype", "emo",
                            "visual_quality", "face_quality", "hook_value", "promo_value",
                            "desc"])
            for c in selected:
                desc = (c.get('desc_clean', c.get('desc', '')) or '')[:80]
                writer.writerow([
                    "Y",
                    int(c.get('ep', '0') or 0),
                    c.get('time', 0),
                    round(float(c.get('suggested_duration', 2.5) or 2.5), 1),
                    c.get('cut_role', '?'),
                    c.get('best_cut', '?'),
                    c.get('_v2', {}).get('event', '?'),
                    c.get('event_subtype', ''),
                    c.get('emotion', 3),
                    c.get('visual_quality', 3),
                    c.get('face_quality', '?'),
                    c.get('hook_value', 1),
                    c.get('promo_value', 1),
                    desc,
                ])
        print(f"  [CSV] 审片表: {csv_path}")

    if dry_run:
        # 生成模拟 clip_specs 用于 timeline 预览
        mdef_dr = MOLECULE_TYPES[mol_type]
        clip_dur_range = mdef_dr.get('clip_dur', (3, 5))
        default_dur = (clip_dur_range[0] + clip_dur_range[1]) / 2
        dry_specs = []
        for i, c in enumerate(selected):
            ep = str(c.get('ep', '1'))
            t = c.get('time', 0)
            start = max(0, t - 1.0)
            dry_specs.append({
                "id": f"{mol_type}_{i+1:02d}_EP{int(ep):02d}",
                "ep": ep,
                "start": round(start, 1),
                "dur": round(default_dur, 1),
            })
        timeline_path = write_timeline_file(mol_type, selected, dry_specs, final_path=None, duration=None)
        print(f"  [Dry Run] 跳过切割")
        print(f"  Timeline: {timeline_path}")
        return selected

    # 切割
    clip_specs, results, ok = cut_molecular_clips(selected, mol_type)
    print(f"  切割: {ok}/{len(results)} 成功")

    effective_min = min(mdef["min_clips"], max(8, ok // 2))
    if ok < effective_min:
        print(f"  [ERR] 有效片段不足")
        return None

    # 组装
    final = assemble_molecular(clip_specs, results, mol_type, xfade_type=xfade_type)
    if final and os.path.exists(final):
        dur = float(subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", final], capture_output=True, text=True
        ).stdout.strip())
        timeline_path = write_timeline_file(mol_type, selected, clip_specs, final, dur)
        qa_path = write_qa_report(mol_type, selected, clip_specs, final, dur, timeline_path=timeline_path)
        print(f"  输出: {final}")
        print(f"  QA: {qa_path}")
        print(f"  Timeline: {timeline_path}")
        print(f"  时长: {dur:.1f}s")
        return final
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='分子级拆解宣发生成器')
    parser.add_argument('--type', type=str, default='', help='指定分子类型 (如 hook_clash)')
    parser.add_argument('--types', type=str, default='', help='指定多个分子类型，逗号分隔')
    parser.add_argument('--dry-run', action='store_true', help='仅展示选片，不切割')
    parser.add_argument('--export-csv', action='store_true', help='dry-run 同时导出 CSV 审片表')
    parser.add_argument('--from-csv', type=str, default='', help='从人工审核后的 CSV 读取选片并生成')
    parser.add_argument('--xfade', type=str, default='fade',
                        choices=['fade', 'pixelize', 'smoothleft', 'none'],
                        help='片段间转场效果 (默认 fade)')
    parser.add_argument('--quick', action='store_true', help='快速模式: 跳过 xfade + legacy 数据')
    args = parser.parse_args()

    if args.quick:
        args.xfade = 'none'

    # ── CSV 人工审核模式 ──
    review_selections = None
    if args.from_csv:
        if not os.path.exists(args.from_csv):
            print(f"[ERR] CSV文件不存在: {args.from_csv}")
            return
        review_selections = {}
        with open(args.from_csv, 'r', encoding='utf-8-sig') as cf:
            reader = csv.DictReader(cf)
            for row in reader:
                keep = str(row.get('keep(Y/N)', 'Y')).strip().upper()
                if keep not in ('Y', 'YES', '1'):
                    continue
                try:
                    ep = int(row.get('ep', '0'))
                    t = int(float(row.get('time(s)', '0')))
                    review_selections[(ep, t)] = True
                except (ValueError, TypeError):
                    continue
        print(f"  从CSV读取 {len(review_selections)} 条确认片段")
        if not review_selections:
            print("[ERR] CSV中无有效Y标记片段")
            return

    t_start = time.time()
    print("=" * 60)
    print(f"  {PROJECT_NAME} 分子级拆解宣发")
    print("=" * 60)

    # 加载数据
    print("\n[加载] 审片数据...")
    clips = load_analysis()
    print(f"  共 {len(clips)} 条审片记录")

    if args.types:
        mol_types = [x.strip() for x in args.types.split(',') if x.strip()]
    elif args.type:
        mol_types = [args.type]
    else:
        mol_types = list(MOLECULE_TYPES.keys())
    invalid = [mt for mt in mol_types if mt not in MOLECULE_TYPES]
    if invalid:
        print(f"  未知分子类型: {', '.join(invalid)}")
        print(f"  可用: {', '.join(MOLECULE_TYPES.keys())}")
        return

    # CSV 审核模式：只支持单类型
    if review_selections is not None and len(mol_types) > 1:
        print("[WARN] CSV审核模式一次只支持单个分子类型，仅处理第一个")
        mol_types = mol_types[:1]

    export_csv = args.export_csv or bool(args.from_csv)  # from_csv 也隐式导出

    results = []
    for mt in mol_types:
        try:
            r = process_molecule(clips, mt, dry_run=args.dry_run,
                                export_csv=export_csv,
                                review_selections=review_selections,
                                xfade_type=args.xfade)
            results.append(r)
        except Exception as e:
            print(f"  [ERR] {mt} 失败: {e}")
            import traceback
            traceback.print_exc()

    if not args.dry_run or args.export_csv:
        succeeded = sum(1 for r in results if r)
        print(f"\n{'='*60}")
        print(f"  完成! {succeeded}/{len(mol_types)} 条生成成功")
        print(f"  耗时: {time.time() - t_start:.0f}s")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
