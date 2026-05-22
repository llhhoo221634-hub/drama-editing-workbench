"""
selection_constraints.py — 硬约束过滤、片段去重与 legacy 数据检测
"""
from molecule_types import MOLECULE_TYPES, clip_event_text, molecular_score


def _is_legacy_data(clips):
    """检测数据集是否为 legacy（缺少 V2 音频字段）。
    如果超过 80% 的条目没有 audio_energy 字段，视为 legacy。"""
    if not clips:
        return True
    has_audio = sum(1 for c in clips if c.get('audio_energy', 0) > 1 or c.get('speech_density', 0) > 1)
    return has_audio < len(clips) * 0.2


def _molecular_filter_pass(clips, mol_type, strict=True):
    """单次过滤。strict=True 为正常模式，strict=False 为降级模式（放宽阈值）。"""
    mdef = MOLECULE_TYPES[mol_type]
    pool = []

    # 降级模式：emotion 阈值降低 1 级，跳过事件强匹配和人脸要求
    emo_min = mdef.get('emo_min', 3)
    if not strict:
        emo_min = max(2, emo_min - 1)

    for c in clips:
        emo = c.get('emotion', 3)
        if emo < emo_min:
            continue
        if c.get('usable') is False:
            continue
        if c.get('reject_reason', '无') != '无':
            continue
        if c.get('event_conf', '') == '模糊' and c.get('visual_quality', 3) <= 2:
            continue
        if c.get('visual_quality', 3) < 3:
            continue
        desc = c.get('desc_clean', c.get('desc', ''))
        if any(k in desc for k in ['似乎', '可能', '大概', '仿佛', '看起来像', '似在']):
            continue

        # 人脸要求：降级模式下跳过
        if strict:
            if mol_type != 'cinematic_beauty' and c.get('face_quality', '') in ['模糊', '无人']:
                continue
            if mol_type != 'cinematic_beauty' and not c.get('face_quality') and c.get('faces', 0) < 1:
                continue
        if mol_type == 'cinematic_beauty' and c.get('face_quality', '') == '模糊':
            continue
        # cinematic_beauty: 排除低质量帧
        if mol_type == 'cinematic_beauty' and c.get('face_quality', '') == '模糊':
            continue
        # 排除 V1 legacy 数据（desc < 30 字 = 旧版扫描数据）
        desc_len = len(c.get('desc_clean', '') or c.get('desc', '') or '')
        if desc_len < 30:
            continue


        # 事件过滤：降级模式下改为加分而非硬过滤
        target_events = mdef.get('events')
        if target_events and strict:
            event_text = clip_event_text(c)
            if not any(e in event_text for e in target_events):
                continue

        # 景别过滤 (光影美学)：降级模式下跳过
        target_shots = mdef.get('shots')
        if target_shots and strict:
            shot = c.get('_v2', {}).get('shot', '') or ''
            scene_types = c.get('scene_types', [])
            if not any(s in shot or s in str(scene_types) for s in target_shots):
                continue

        pool.append(c)

    return pool


def molecular_filter(clips, mol_type):
    """按分子类型筛选片段池。
    对 legacy 数据自动降级：先尝试严格过滤，不足则放宽阈值。"""
    mdef = MOLECULE_TYPES[mol_type]
    min_needed = mdef.get('min_clips', 3)
    legacy = _is_legacy_data(clips)

    # 先尝试严格过滤
    pool = _molecular_filter_pass(clips, mol_type, strict=True)

    # 如果严格过滤结果不足，且数据为 legacy，降级重试
    if len(pool) < min_needed and legacy:
        pool_relaxed = _molecular_filter_pass(clips, mol_type, strict=False)
        if len(pool_relaxed) > len(pool):
            print(f"    [降级] legacy数据严格过滤仅{len(pool)}条，放宽后{len(pool_relaxed)}条")
            pool = pool_relaxed

    # 最终兜底：如果仍然不足，取 top-N by score（不做硬过滤）
    if len(pool) < min_needed and legacy:
        usable = [c for c in clips if c.get('usable', True) and c.get('reject_reason', '无') == '无']
        if len(usable) > len(pool):
            scored = sorted(usable, key=lambda c: -molecular_score(c, mol_type))
            pool = scored[:max(min_needed * 3, 20)]
            print(f"    [兜底] 按评分取 top-{len(pool)} 候选")

    return pool


def deduplicate_clips(clips, window_seconds=5):
    """通用片段去重：同集且时间窗口内的片段只保留第一个。
    window_seconds: 时间窗口大小（秒），同集内时间差小于此值视为重复。
    返回去重后的列表（保持原始顺序）。"""
    kept = []
    used_windows = set()
    for c in clips:
        ep = int(c.get('ep', '0') or 0)
        t = c.get('time', 0)
        key = (ep, round(t / window_seconds))
        if key in used_windows:
            continue
        used_windows.add(key)
        kept.append(c)
    return kept


def apply_hard_constraints(ranked, target_count=None, max_per_ep=4, max_same_shot=2, min_time_apart=8, max_per_scene=2):
    """硬约束贪心过滤：EP上限/景别连续/时间间隔/场景分散/时间桶各≥1"""
    if not ranked:
        return []
    all_eps = sorted(set(int(c.get('ep', '1') or 1) for c in ranked))
    n_buckets = 6
    min_ep = all_eps[0]
    ep_range = max(1, all_eps[-1] - min_ep + 1)
    bucket_size = max(1, ep_range // n_buckets)

    def _bucket(ep):
        return min(n_buckets - 1, (ep - min_ep) // bucket_size)

    def _shot(c):
        return (c.get('_v2', {}) or {}).get('shot', '') or ''

    def _scene(c):
        return ((c.get('_v2', {}) or {}).get('scene', '') or '')[:10]

    result = []
    ep_cnt = {}
    scene_cnt = {}
    shot_hist = []
    bucket_cov = set()

    def _violates(c, ep, t, shot, skey):
        if ep_cnt.get(ep, 0) >= max_per_ep:
            return True
        if len(shot_hist) >= max_same_shot and all(s == shot for s in shot_hist[-max_same_shot:]):
            return True
        ep_times = [rc.get('time', 0) for rc in result if int(rc.get('ep', '0') or 0) == ep]
        if any(abs(t - pt) < min_time_apart for pt in ep_times):
            return True
        if skey and scene_cnt.get(skey, 0) >= max_per_scene:
            return True
        return False

    for c in ranked:
        ep = int(c.get('ep', '1') or 1)
        t = c.get('time', 0)
        shot = _shot(c)
        skey = _scene(c)
        if _violates(c, ep, t, shot, skey):
            continue
        result.append(c)
        ep_cnt[ep] = ep_cnt.get(ep, 0) + 1
        if skey:
            scene_cnt[skey] = scene_cnt.get(skey, 0) + 1
        shot_hist.append(shot)
        bucket_cov.add(_bucket(ep))
        if target_count and len(result) >= target_count:
            break

    if len(bucket_cov) < n_buckets:
        for c in ranked:
            if c in result or _bucket(int(c.get('ep', '1') or 1)) in bucket_cov:
                continue
            ep = int(c.get('ep', '1') or 1)
            t = c.get('time', 0)
            shot = _shot(c)
            skey = _scene(c)
            if _violates(c, ep, t, shot, skey):
                continue
            result.append(c)
            ep_cnt[ep] = ep_cnt.get(ep, 0) + 1
            if skey:
                scene_cnt[skey] = scene_cnt.get(skey, 0) + 1
            shot_hist.append(shot)
            bucket_cov.add(_bucket(ep))
            if len(bucket_cov) >= n_buckets:
                break
    return result
