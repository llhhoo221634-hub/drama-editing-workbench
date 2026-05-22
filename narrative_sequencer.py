"""
narrative_sequencer.py — 叙事序列优化，按叙事功能分三段重排帧顺序
"""

def narrative_order(selected_clips):
    """Phase 3: 叙事序列优化 — 按叙事功能分三段重排帧顺序。
    前10%=hook/inciting帧，中间70%=rising→climax，后20%=setup/transition向悬念倾斜。"""
    if not selected_clips or len(selected_clips) < 3:
        return selected_clips
    n = len(selected_clips)
    # 确保 _nf 标签存在；检测 action_direction / emotion_trend
    for c in selected_clips:
        c.setdefault('_nf', 'rising')
        c.setdefault('action_direction', 'neutral')
        c.setdefault('emotion_trend', 'stable')
    buckets = {'hook': [], 'inciting': [], 'rising': [], 'climax': [],
               'setup': [], 'transition': []}
    for c in selected_clips:
        nf = str(c.get('_nf', 'rising')).lower()
        buckets.get(nf, buckets['rising']).append(c)
    front_n = max(1, round(n * 0.10))
    tail_n = max(1, round(n * 0.20))
    mid_n = n - front_n - tail_n
    # 前段: hook + inciting
    front = buckets['hook'] + buckets['inciting']
    if len(front) < front_n:
        front += buckets['climax'][:front_n - len(front)]
    front = front[:front_n]
    used = {id(c) for c in front}
    # 中段: rising → climax
    middle = [c for c in (buckets['rising'] + buckets['climax']) if id(c) not in used]
    if len(middle) < mid_n:
        extra = [c for c in selected_clips if id(c) not in used and c not in middle]
        middle += extra[:mid_n - len(middle)]
    middle = middle[:mid_n]
    used.update(id(c) for c in middle)
    # 尾段: setup + transition，向悬念倾斜（emotion降序）
    tail = [c for c in (buckets['setup'] + buckets['transition']) if id(c) not in used]
    tail.sort(key=lambda c: -(c.get('emotion', 3)))
    if len(tail) < tail_n:
        extra = [c for c in selected_clips if id(c) not in used and c not in tail]
        tail += sorted(extra, key=lambda c: -(c.get('emotion', 3)))[:tail_n - len(tail)]
    tail = tail[:tail_n]
    result = front + middle + tail
    # 兜底：补全遗漏元素
    result_ids = {id(c) for c in result}
    for c in selected_clips:
        if id(c) not in result_ids:
            result.append(c)
    return result
