"""
genre_engine.py — 剧集类型识别 + 多轮比对评分
可独立运行，也可被 edit_utils.py 导入。
"""
import json, os, sys, re, random, math
from collections import Counter
from pathlib import Path

# 如果被导入，复用 edit_utils 的核心函数
try:
    from edit_utils import parse_vision_line, score_clip, rank_clips, SCENE_RARITY
except ImportError:
    # 独立运行时的最小实现
    SCENE_RARITY = {}
    def parse_vision_line(line):
        emo = 3
        m = re.search(r'情绪[评分：:]*\s*(\d+)', line)
        if m: emo = int(m.group(1))
        return {"emotion": emo, "scene_types": [], "has_content": len(line) > 5,
                "desc_clean": line, "desc_length": len(line),
                "dialogue_lines": 0, "faces": 0}
    def score_clip(c): return 100.0
    def rank_clips(c, **kw): return c[:15]

# ═══════════════════════════════════════════
# 1. 剧集类型体系
# ═══════════════════════════════════════════

# 类型关键词指纹 —— 每种类型在分析文本中的特征词
# strong: 强信号(独属于此类型), weak: 弱信号, anti: 排除信号
GENRE_FINGERPRINTS = {
    "horror": {
        "label": "恐怖悬疑",
        "strong": ["恐怖", "诡异", "阴森", "毛骨悚然", "渗人", "吓人", "瘆人", "阴灵",
                   "纸人", "香火", "尸", "棺", "下葬", "回魂", "祭坛"],
        "weak": ["惊恐", "葬礼", "暗影", "黑", "鬼", "灵", "魂", "咒", "夜葬"],
        "anti": ["甜", "爱", "吻", "搞笑", "欢"],
        "scene_bonus": {"恐怖": 2.5, "诡异": 2.0, "葬礼": 1.8, "下葬": 2.0,
                       "祭坛": 1.8, "对峙": 1.2, "独白": 1.0, "特写": 0.8},
        "weights": {"emotion": 5.0, "scene_rare": 2.5, "dialogue": 2.0,
                    "content": 1.5, "faces": 1.0, "desc_len": 0.3}
    },
    "action_historical": {
        "label": "古装权谋/动作",
        "strong": ["打斗", "战斗", "激烈交锋", "牢中", "囚室", "狱", "剑", "刀",
                   "兵", "战", "刺", "权", "谋", "廷", "帝", "王", "将", "军",
                   "气势逼人", "迅猛", "字幕"],
        "weak": ["对峙", "杀", "血", "死", "怒", "吼", "攻", "红", "衣", "逼人"],
        "anti": ["恐怖", "诡异", "甜", "搞笑"],
        "scene_bonus": {"战斗": 2.5, "打斗": 2.3, "对峙": 1.5, "特写": 1.2,
                       "群像": 1.3, "独白": 1.2, "空镜": 0.6},
        "weights": {"emotion": 3.5, "scene_rare": 2.0, "dialogue": 2.5,
                    "content": 1.5, "faces": 1.5, "desc_len": 0.3}
    },
    "romance": {
        "label": "甜宠/言情",
        "strong": ["吻", "拥抱", "依偎", "牵手", "甜", "甜蜜", "宠", "婚",
                   "约", "爱意", "温柔对视", "谈恋爱", "心动"],
        "weak": ["笑", "暖", "花", "泪", "心", "靠近", "对视", "情侣"],
        "anti": ["打斗", "战斗", "恐怖", "诡异", "杀", "死", "血", "棺"],
        "scene_bonus": {"特写": 2.0, "对视": 2.5, "独白": 1.5, "对话": 1.3,
                       "日常": 1.0, "空镜": 0.8, "群像": 0.6},
        "weights": {"emotion": 3.0, "scene_rare": 1.5, "dialogue": 3.5,
                    "content": 2.0, "faces": 2.5, "desc_len": 0.3}
    },
    "comedy": {
        "label": "喜剧/搞笑",
        "strong": ["搞笑", "喜剧", "滑稽", "夸张表情", "鬼脸", "逗", "闹剧",
                   "耍宝", "囧", "尬", "出糗", "整蛊"],
        "weak": ["笑", "乐", "欢", "趣", "逗", "闹"],
        "anti": ["恐怖", "诡异", "阴森", "杀", "死", "血"],
        "scene_bonus": {"特写": 1.8, "对话": 1.5, "日常": 1.2, "群像": 1.3,
                       "空镜": 0.4, "对峙": 0.8},
        "weights": {"emotion": 2.5, "scene_rare": 1.0, "dialogue": 3.5,
                    "content": 1.5, "faces": 2.0, "desc_len": 0.3}
    },
    "mystery": {
        "label": "悬疑/推理",
        "strong": ["悬疑", "谜", "线索", "查", "追踪", "秘密", "真相", "案",
                   "证据", "审问", "探", "窥", "偷", "暗查"],
        "weak": ["隐", "藏", "问", "疑", "奇怪", "不对劲"],
        "anti": ["搞笑", "甜", "宠", "吻"],
        "scene_bonus": {"对峙": 2.0, "特写": 1.5, "独白": 1.8, "对话": 1.3,
                       "日常": 0.8, "空镜": 1.0},
        "weights": {"emotion": 3.0, "scene_rare": 1.8, "dialogue": 3.0,
                    "content": 2.0, "faces": 1.5, "desc_len": 0.3}
    },
}

# 统一标准（跨类型 baseline）
UNIFIED_WEIGHTS = {"emotion": 3.5, "scene_rare": 1.8, "dialogue": 2.5,
                   "content": 1.5, "faces": 1.5, "desc_len": 0.3}
UNIFIED_SCENE_BONUS = {}  # 用默认 SCENE_RARITY


# ═══════════════════════════════════════════
# 2. 剧集类型识别器
# ═══════════════════════════════════════════

class GenreDetector:
    """从审片分析数据中识别剧集类型"""

    def __init__(self, sample_size=100):
        self.sample_size = sample_size
        self.genres = GENRE_FINGERPRINTS

    def analyze(self, analysis_file):
        """
        分析审片文件 → 返回类型识别结果。
        使用行覆盖率: 多少比例的行包含该类型的关键词。
        strong 关键词权重 3x, weak 1x, anti 扣分。
        """
        # 读取所有行
        lines = []
        with open(analysis_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    parts = line.split(' ', 2)
                    desc = parts[2] if len(parts) > 2 else line
                    lines.append(desc)

        # 随机采样
        if len(lines) > self.sample_size:
            sample = random.sample(lines, self.sample_size)
        else:
            sample = lines

        total_lines = len(sample)
        scores = {}
        details = {}

        for gid, ginfo in self.genres.items():
            strong_hits = 0
            weak_hits = 0
            anti_hits = 0
            strong_detail = Counter()
            weak_detail = Counter()
            anti_detail = Counter()

            for line in sample:
                # strong 关键词: 该行包含任意 strong 词 → +3
                line_strong = [kw for kw in ginfo.get('strong', []) if kw in line]
                if line_strong:
                    strong_hits += 1
                    for kw in line_strong:
                        strong_detail[kw] += 1

                # weak 关键词: 该行包含任意 weak 词 → +1
                line_weak = [kw for kw in ginfo.get('weak', []) if kw in line]
                if line_weak:
                    weak_hits += 1
                    for kw in line_weak:
                        weak_detail[kw] += 1

                # anti 关键词: 该行包含任意 anti 词 → -2
                line_anti = [kw for kw in ginfo.get('anti', []) if kw in line]
                if line_anti:
                    anti_hits += 1
                    for kw in line_anti:
                        anti_detail[kw] += 1

            # 综合分数: strong覆盖率*3 + weak覆盖率*1 - anti覆盖率*2
            score = (strong_hits / max(1, total_lines)) * 3.0 \
                  + (weak_hits / max(1, total_lines)) * 1.0 \
                  - (anti_hits / max(1, total_lines)) * 2.0

            scores[gid] = max(0, score)
            details[gid] = {
                "label": ginfo['label'],
                "score": round(scores[gid], 3),
                "strong_coverage": f"{strong_hits}/{total_lines}",
                "weak_coverage": f"{weak_hits}/{total_lines}",
                "anti_coverage": f"{anti_hits}/{total_lines}",
                "top_strong": strong_detail.most_common(5),
                "top_anti": anti_detail.most_common(3),
            }

        # 排序
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        primary_id, primary_score = ranked[0]
        secondary_id, secondary_score = ranked[1] if len(ranked) > 1 else (None, 0)

        # 置信度：第一名分数 / 第二名分数
        confidence = primary_score / max(0.01, secondary_score) if secondary_score > 0 else 3.0
        confidence = round(min(confidence, 5.0), 2)

        return {
            "primary_genre": primary_id,
            "primary_label": self.genres[primary_id]['label'],
            "primary_score": round(primary_score, 3),
            "secondary_genre": secondary_id,
            "secondary_label": self.genres[secondary_id]['label'] if secondary_id else None,
            "secondary_score": round(secondary_score, 3) if secondary_score else 0,
            "confidence": confidence,
            "all_scores": {gid: details[gid] for gid in scores},
            "sample_size": len(sample),
            "total_lines": len(lines),
        }

    def get_weights(self, analysis_file):
        """自动检测类型 → 返回推荐权重"""
        result = self.analyze(analysis_file)
        gid = result['primary_genre']
        ginfo = self.genres.get(gid, {})
        return {
            "weights": ginfo.get('weights', UNIFIED_WEIGHTS),
            "scene_bonus": ginfo.get('scene_bonus', {}),
            "genre_label": ginfo.get('label', '未知'),
            "confidence": result['confidence'],
            "detection": result,
        }


# ═══════════════════════════════════════════
# 3. 多轮比对评分引擎
# ═══════════════════════════════════════════

def multi_pass_rank(clips, genre_config, n_passes=5, jitter=0.15):
    """
    多轮评分比对：
      - 第1轮: 使用检测到的类型权重
      - 第2轮: 使用统一标准权重
      - 第3-5轮: 在类型权重基础上随机微调 ±jitter

    返回:
      consensus: 在所有轮次中都出现在 Top-N 的片段
      all_rankings: 每轮的完整排名
      stability: 每个片段的排名稳定性(标准差越小越稳定)
    """
    base_weights = genre_config.get('weights', UNIFIED_WEIGHTS)
    scene_bonus = genre_config.get('scene_bonus', {})

    # 临时替换 SCENE_RARITY 为类型特化版本
    original_rarity = SCENE_RARITY.copy() if SCENE_RARITY else {}

    all_rankings = []
    weight_configs = []

    # ── 第1轮: 类型权重 ──
    # 合并 scene_bonus 到 SCENE_RARITY
    effective_rarity = {**original_rarity, **scene_bonus}
    # 使用临时 monkey-patch 的方式不太优雅，直接传参数
    w1 = dict(base_weights)
    all_rankings.append(_rank_with_weights(clips, w1, effective_rarity))
    weight_configs.append({"label": "类型权重", "weights": w1})

    # ── 第2轮: 统一标准 ──
    w2 = dict(UNIFIED_WEIGHTS)
    all_rankings.append(_rank_with_weights(clips, w2, original_rarity if original_rarity else {}))
    weight_configs.append({"label": "统一标准", "weights": w2})

    # ── 第3-5轮: 微调 ──
    for i in range(n_passes - 2):
        w_jitter = {}
        for k, v in base_weights.items():
            w_jitter[k] = round(v * (1 + random.uniform(-jitter, jitter)), 2)
        all_rankings.append(_rank_with_weights(clips, w_jitter, effective_rarity))
        weight_configs.append({"label": f"微调轮{i+1}", "weights": w_jitter})

    # ── 计算共识 ──
    # 统计每个片段在各轮的排名
    clip_ranks = {}  # {clip_id: [rank1, rank2, ...]}
    for rank_list in all_rankings:
        for rank_idx, clip in enumerate(rank_list):
            cid = f"EP{clip.get('ep','?')}_{clip.get('time','?')}s"
            if cid not in clip_ranks:
                clip_ranks[cid] = {"clip": clip, "ranks": [], "appearances": 0}
            clip_ranks[cid]["ranks"].append(rank_idx + 1)
            clip_ranks[cid]["appearances"] += 1

    # 计算稳定性（排名标准差，越小越稳定）
    import statistics
    for cid, data in clip_ranks.items():
        ranks = data["ranks"]
        data["mean_rank"] = round(statistics.mean(ranks), 1)
        data["std_rank"] = round(statistics.stdev(ranks), 1) if len(ranks) > 1 else 0
        data["consensus_score"] = data["appearances"] / n_passes

    # 共识片段：出现在所有轮次且排名都在 Top-40（扩大池以覆盖后期集数）
    top_n = 40
    consensus = []
    for cid, data in clip_ranks.items():
        in_all = data["appearances"] >= n_passes
        all_top = all(r <= top_n for r in data["ranks"])
        if in_all and all_top:
            consensus.append({
                "id": cid,
                "clip": data["clip"],
                "mean_rank": data["mean_rank"],
                "std_rank": data["std_rank"],
                "stability": "high" if data["std_rank"] < 3 else "medium",
            })

    # 按平均排名排序
    consensus.sort(key=lambda x: x["mean_rank"])

    # 找出分歧点（排名波动大的片段）
    divergences = []
    for cid, data in clip_ranks.items():
        if data["std_rank"] > 5 and len(data["ranks"]) >= 3:
            divergences.append({
                "id": cid,
                "clip": data["clip"],
                "ranks": data["ranks"],
                "std_rank": data["std_rank"],
            })
    divergences.sort(key=lambda x: -x["std_rank"])

    return {
        "consensus": consensus,
        "divergences": divergences[:10],
        "all_rankings": all_rankings,
        "weight_configs": weight_configs,
        "n_passes": n_passes,
        "total_clips": len(clips),
    }


def _rank_with_weights(clips, weights, scene_bonus):
    """用指定权重评分并排序"""
    import copy
    scored = []
    for c in clips:
        cc = copy.copy(c)
        # 临时注入场景加成
        cc['_scene_bonus'] = scene_bonus
        cc['_score'] = _score_with_config(cc, weights, scene_bonus)
        scored.append(cc)

    scored.sort(key=lambda x: x['_score'], reverse=True)

    # 多样性筛选（同一集最多2个）
    result = []
    ep_count = {}
    for c in scored:
        ep = str(c.get('ep', '?'))
        ep_count[ep] = ep_count.get(ep, 0)
        if ep_count[ep] < 2:
            result.append(c)
            ep_count[ep] += 1
        if len(result) >= 30:
            break
    return result


def _score_with_config(clip, weights, scene_bonus):
    """使用特定权重和场景加成评分"""
    w = weights
    score = 0.0
    emotion = clip.get('emotion', 3)
    emotion_map = {1: 0, 2: 5, 3: 15, 4: 40, 5: 70}
    score += emotion_map.get(emotion, 15) * w.get('emotion', 3.5)

    scene_types = clip.get('scene_types', [])
    rarity_sum = sum(scene_bonus.get(st, SCENE_RARITY.get(st, 1.0)) for st in scene_types)
    score += min(30, rarity_sum * 5) * w.get('scene_rare', 1.8)

    score += min(40, clip.get('dialogue_lines', 0) * 8) * w.get('dialogue', 2.5)
    if clip.get('has_content', False):
        score += 12 * w.get('content', 1.5)
    score += min(16, clip.get('faces', 0) * 4) * w.get('faces', 1.5)

    import math
    desc_len = clip.get('desc_length', 0)
    score += min(5, math.log(max(1, desc_len)) * 1.5) * w.get('desc_len', 0.3)

    return round(score, 1)


# ═══════════════════════════════════════════
# 4. 一键入口: 类型识别 → 多轮比对 → 推荐
# ═══════════════════════════════════════════

def analyze_and_rank(analysis_file, n_passes=5, verbose=True):
    """
    输入审片文件 → 输出类型 + 共识排名。
    这是主要工作流入口。
    """
    # 加载数据
    clips = []
    with open(analysis_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            parts = line.split(' ', 2)
            if len(parts) < 3: continue
            ep_str = parts[0].replace('ep', '')
            time_str = parts[1].replace('s', '')
            desc = parts[2]
            parsed = parse_vision_line(desc)
            parsed["ep"] = ep_str
            parsed["time"] = int(time_str) if time_str.isdigit() else 0
            clips.append(parsed)

    if verbose:
        print(f"加载 {len(clips)} 条审片记录")

    # 步骤1: 类型识别
    detector = GenreDetector()
    genre_result = detector.analyze(analysis_file)
    genre_config = detector.get_weights(analysis_file)

    if verbose:
        print(f"\n类型识别: {genre_result['primary_label']} "
              f"(置信度 {genre_result['confidence']}, "
              f"次选: {genre_result.get('secondary_label', 'N/A')})")
        print(f"分数详情:")
        for gid, detail in genre_result['all_scores'].items():
            if detail['score'] > 0:
                strong_kw = [f"{k}({v})" for k, v in detail['top_strong'][:3]]
                anti_kw = [f"{k}({v})" for k, v in detail.get('top_anti', [])[:2]]
                anti_str = f"  anti: {', '.join(anti_kw)}" if anti_kw else ""
                print(f"  {detail['label']}: {detail['score']:.3f} "
                      f"strong={detail['strong_coverage']} weak={detail['weak_coverage']}"
                      f"{anti_str} → {', '.join(strong_kw)}")

    # 步骤2: 多轮比对
    if verbose:
        print(f"\n多轮比对评分 (n={n_passes})...")
    result = multi_pass_rank(clips, genre_config, n_passes=n_passes)

    if verbose:
        print(f"\n共识片段 (Top {len(result['consensus'])} 个, 所有轮次均入选):")
        for i, c in enumerate(result['consensus'][:15]):
            clip = c['clip']
            scenes = '/'.join(clip.get('scene_types', [])[:3]) or '-'
            print(f"  {i+1:2d}. {c['id']} avg_rank={c['mean_rank']:.1f} "
                  f"std={c['std_rank']:.1f} emo={clip['emotion']} "
                  f"scenes=[{scenes}]")

        if result['divergences']:
            print(f"\n分歧片段 (排名波动大, 建议人工确认):")
            for d in result['divergences'][:5]:
                print(f"  {d['id']} ranks={d['ranks']} std={d['std_rank']:.1f}")

    return {
        "genre": genre_result,
        "ranking": result,
        "clips": clips,
    }


# ═══════════════════════════════════════════
# 5. 类型 → 剪辑配置自动映射
# ═══════════════════════════════════════════

EDIT_CONFIGS = {
    "horror": {
        "color_grade": "eq=contrast=1.15:saturation=0.75:brightness=-0.08,colorbalance=rs=-0.08:gs=0:bs=0.12",
        "bgm_style": "dark_ambient",  # 低频压迫感
        "transition": "hard_cut",      # 硬切，不拖泥带水
        "pacing": "fast_then_slow",    # 快节奏开场 → 慢节奏铺垫 → 爆发
        "clip_dur": (3, 8),           # 片段时长范围
        "hook_placement": "first_3s",  # 钩子位置
        "caption_style": "minimal",    # 少字幕，靠画面
    },
    "action_historical": {
        "color_grade": "eq=contrast=1.25:saturation=1.1:gamma=1.05,colorbalance=rs=0.02:gs=-0.02:bs=-0.05",
        "bgm_style": "epic_orchestral",
        "transition": "hard_cut",
        "pacing": "fast_throughout",
        "clip_dur": (2, 5),
        "hook_placement": "first_2s",
        "caption_style": "bold_impact",  # 大字幕冲击
    },
    "romance": {
        "color_grade": "eq=contrast=1.05:saturation=1.15:brightness=0.03,colorbalance=rs=0.05:gs=-0.02:bs=-0.05",
        "bgm_style": "sweet_piano",
        "transition": "soft_crossfade",
        "pacing": "slow_build",
        "clip_dur": (5, 10),
        "hook_placement": "after_3s",
        "caption_style": "elegant",
    },
    "comedy": {
        "color_grade": "eq=contrast=1.1:saturation=1.2:brightness=0.05",
        "bgm_style": "upbeat_funny",
        "transition": "hard_cut",
        "pacing": "fast_punchy",
        "clip_dur": (2, 4),
        "hook_placement": "first_1s",
        "caption_style": "playful",
    },
    "mystery": {
        "color_grade": "eq=contrast=1.1:saturation=0.85:brightness=-0.03,colorbalance=rs=-0.03:gs=0:bs=0.06",
        "bgm_style": "tense_mystery",
        "transition": "slow_fade",
        "pacing": "steady_build",
        "clip_dur": (4, 8),
        "hook_placement": "first_3s",
        "caption_style": "clean",
    },
    # 适配对方系统的7类短剧体系（扩展到我们实际场景）
    "revenge": {
        "label": "复仇打脸",
        "color_grade": "eq=contrast=1.3:saturation=1.0:gamma=1.1",
        "bgm_style": "tense_revenge",
        "transition": "hard_cut",
        "pacing": "fast_aggressive",
        "clip_dur": (2, 4),
        "hook_placement": "first_1s",
        "caption_style": "bold_impact",
    },
    "family": {
        "label": "家庭伦理",
        "color_grade": "eq=contrast=1.05:saturation=0.95:brightness=0.02",
        "bgm_style": "emotional_family",
        "transition": "soft_crossfade",
        "pacing": "slow_dramatic",
        "clip_dur": (5, 10),
        "hook_placement": "first_5s",
        "caption_style": "clean",
    },
    "urban": {
        "label": "都市逆袭",
        "color_grade": "eq=contrast=1.2:saturation=1.1:gamma=1.05",
        "bgm_style": "epic_urban",
        "transition": "hard_cut",
        "pacing": "fast_momentum",
        "clip_dur": (2, 5),
        "hook_placement": "first_2s",
        "caption_style": "bold_impact",
    },
}


# ═══════════════════════════════════════════
# 5.5 8大剪辑方向 + 类型→方向智能推荐
# ═══════════════════════════════════════════

EDIT_DIRECTIONS = {
    "promo": {
        "name": "宣传片",
        "desc": "整体剧情浓缩、高级质感、大气混剪、引流预告",
        "duration": (40, 60),
        "clip_dur": (3, 7),
        "num_clips": (6, 10),
        "pacing": "慢起→高潮→收尾",
        "transition": "crossfade",
        "bgm_vol": 0.25,
        "hook": "前3秒最炸裂画面",
        "caption": "高级简约",
    },
    "highlight_mix": {
        "name": "爽点混剪",
        "desc": "只剪高能/打脸/反转/冲突，无废话，全程高能",
        "duration": (25, 40),
        "clip_dur": (1.5, 4),
        "num_clips": (8, 15),
        "pacing": "全程爆炸",
        "transition": "hard_cut",
        "bgm_vol": 0.35,
        "hook": "开场即高潮",
        "caption": "醒目大字",
    },
    "first_person": {
        "name": "第一人称切片",
        "desc": "沉浸式代入主角视角、情绪跟随、氛围感强",
        "duration": (35, 55),
        "clip_dur": (4, 10),
        "num_clips": (5, 8),
        "pacing": "铺垫→沉浸→爆发",
        "transition": "soft_fade",
        "bgm_vol": 0.2,
        "hook": "情绪钩子(非视觉冲击)",
        "caption": "克制",
    },
    "third_person": {
        "name": "第三人称切片",
        "desc": "上帝视角客观解说、完整剧情线、适合影视解说号",
        "duration": (40, 65),
        "clip_dur": (5, 10),
        "num_clips": (6, 10),
        "pacing": "平稳叙事",
        "transition": "soft_crossfade",
        "bgm_vol": 0.2,
        "hook": "悬念前置",
        "caption": "清晰易读",
    },
    "role_cut": {
        "name": "角色单人Cut",
        "desc": "只剪某一人物的完整高光线、粉丝向",
        "duration": (30, 50),
        "clip_dur": (3, 8),
        "num_clips": (5, 10),
        "pacing": "人物弧光递进",
        "transition": "soft_crossfade",
        "bgm_vol": 0.25,
        "hook": "角色高光时刻",
        "caption": "人设标签",
    },
    "episode_split": {
        "name": "剧情分集",
        "desc": "自动拆分为30-60秒单集，适配短剧平台分发",
        "duration": (30, 60),
        "clip_dur": (4, 8),
        "num_clips": (5, 8),
        "pacing": "一集一冲突，钩子前置",
        "transition": "hard_cut",
        "bgm_vol": 0.2,
        "hook": "每集开头3秒钩子",
        "caption": "集数标记",
    },
    "emotion_focus": {
        "name": "情绪向",
        "desc": "只保留暧昧/虐心/甜宠/压抑情绪片段",
        "duration": (30, 50),
        "clip_dur": (4, 10),
        "num_clips": (5, 8),
        "pacing": "情绪递进、慢镜头+卡点",
        "transition": "slow_fade",
        "bgm_vol": 0.3,
        "hook": "情绪共鸣开场",
        "caption": "氛围感字幕",
    },
    "commentary": {
        "name": "二创解说",
        "desc": "AI写解说词+配音，完整二创成片",
        "duration": (45, 75),
        "clip_dur": (5, 12),
        "num_clips": (6, 10),
        "pacing": "旁白驱动、信息密度高",
        "transition": "soft_crossfade",
        "bgm_vol": 0.15,
        "hook": "悬念/疑问开场",
        "caption": "解说字幕",
    },
}

# 类型 → 推荐方向映射（按优先级排序）
GENRE_DIRECTION_MAP = {
    "horror":              ["first_person", "promo", "highlight_mix", "emotion_focus"],
    "action_historical":   ["promo", "highlight_mix", "third_person", "role_cut"],
    "romance":             ["emotion_focus", "first_person", "role_cut", "promo"],
    "comedy":              ["highlight_mix", "third_person", "episode_split", "role_cut"],
    "mystery":             ["first_person", "third_person", "promo", "commentary"],
    "revenge":             ["highlight_mix", "first_person", "promo", "role_cut"],
    "family":              ["third_person", "emotion_focus", "episode_split", "commentary"],
    "urban":               ["highlight_mix", "promo", "role_cut", "first_person"],
}

# 方向 → 评分权重微调（在类型权重基础上叠加）
DIRECTION_WEIGHT_MODIFIERS = {
    "promo":          {"emotion": 1.1, "scene_rare": 1.1, "faces": 1.1},    # 全面提升
    "highlight_mix":  {"emotion": 1.3, "scene_rare": 1.2, "dialogue": 0.7},  # 情绪优先，对话不重要
    "first_person":   {"emotion": 1.2, "faces": 1.3, "dialogue": 1.1},       # 人物+情绪
    "third_person":   {"dialogue": 1.3, "content": 1.2, "emotion": 0.9},     # 对话+内容
    "role_cut":       {"faces": 1.5, "dialogue": 1.2, "scene_rare": 0.8},    # 人物绝对优先
    "episode_split":  {"emotion": 1.1, "scene_rare": 1.0},                    # 均衡
    "emotion_focus":  {"emotion": 1.4, "content": 1.1, "scene_rare": 0.9},   # 情绪最大化
    "commentary":     {"dialogue": 1.4, "content": 1.3, "emotion": 0.8},     # 对话+内容
}


def recommend_directions(genre_id, top_n=3):
    """根据类型推荐最优剪辑方向"""
    directions = GENRE_DIRECTION_MAP.get(genre_id, ["promo", "third_person", "highlight_mix"])
    result = []
    for did in directions[:top_n]:
        info = EDIT_DIRECTIONS.get(did, {})
        result.append({
            "direction_id": did,
            "name": info.get('name', did),
            "desc": info.get('desc', ''),
            "duration": info.get('duration', (40, 60)),
            "pacing": info.get('pacing', ''),
        })
    return result


def apply_direction_weights(base_weights, direction_id):
    """在类型权重基础上叠加方向权重"""
    import copy
    w = copy.copy(base_weights)
    mods = DIRECTION_WEIGHT_MODIFIERS.get(direction_id, {})
    for k, v in mods.items():
        if k in w:
            w[k] = round(w[k] * v, 2)
    return w


def prefilter_for_direction(clips, direction_id, genre_id=None, target_role_keywords=None):
    """
    方向预筛选: 在评分前先缩小片段池。
    不同方向保留不同类型的片段——这是第一人称/第三人称/角色Cut
    能产出不同结果的核心原因。

    target_role_keywords: 角色Cut模式下，用户指定的角色关键词列表
    """
    if not clips:
        return clips

    if direction_id == "first_person":
        # 第一人称: 高情绪(≥3) + (有对话 或 有对峙/惊恐/诡异/特写场景)
        return [c for c in clips if
                c.get('emotion', 3) >= 3 and
                (c.get('dialogue_lines', 0) > 0 or
                 any(st in c.get('scene_types', [])
                     for st in ['对峙', '惊恐', '诡异', '特写', '恐怖', '紧张', '冲突']))]

    elif direction_id == "third_person":
        # 第三人称: 有实质内容描述 或 有对话(推动剧情的片段)
        # 排除纯情绪评分(无描述)的空洞片段
        return [c for c in clips if
                c.get('has_content', False) or
                c.get('dialogue_lines', 0) > 0 or
                len(c.get('scene_types', [])) >= 2]

    elif direction_id == "role_cut":
        # 角色Cut: 有人物 + (有对话 或 用户指定关键词匹配描述)
        if target_role_keywords:
            return [c for c in clips if
                    c.get('faces', 0) > 0 and
                    (c.get('dialogue_lines', 0) > 0 or
                     any(kw in c.get('desc_clean', '') + c.get('desc', '')
                         for kw in target_role_keywords))]
        else:
            # 未指定角色时: 有人脸 + 对话
            return [c for c in clips if
                    c.get('faces', 0) > 0 and c.get('dialogue_lines', 0) > 0]

    elif direction_id == "episode_split":
        # 剧情分集: 情绪≥3 + 有场景类型(需要明确的断点)
        return [c for c in clips if
                c.get('emotion', 3) >= 3 and
                len(c.get('scene_types', [])) >= 1]

    elif direction_id == "highlight_mix":
        # 爽点混剪: 只要高情绪(≥4)
        return [c for c in clips if c.get('emotion', 3) >= 4]

    elif direction_id == "emotion_focus":
        # 情绪向: 高情绪 + 有实质内容(情绪需要上下文)
        return [c for c in clips if
                c.get('emotion', 3) >= 3 and
                (c.get('has_content', False) or c.get('dialogue_lines', 0) > 0)]

    elif direction_id == "commentary":
        # 二创解说: 有对话 + 有内容(需要素材支撑旁白)
        return [c for c in clips if
                c.get('dialogue_lines', 0) > 0 or
                c.get('has_content', False)]

    else:
        # promo(宣传片) / 未知: 不过滤
        return clips


def detect_episode_split_points(clips, target_dur=45):
    """
    剧情分集模式专用: 自动检测分集断点。
    综合判断: 情绪反转 + 悬念词 + 场景切换 + 台词密度突变
    """
    if not clips:
        return []

    sorted_clips = sorted(clips, key=lambda c: (int(c.get('ep', '0')), c.get('time', 0)))

    # 悬念关键词
    suspense_words = ['到底', '究竟', '没想到', '原来', '竟然', '居然',
                      '怎么可能', '为什么', '是谁', '什么', '真相', '秘密']

    hook_candidates = []
    for i, c in enumerate(sorted_clips):
        score = 0
        desc = c.get('desc_clean', '') + c.get('desc', '')

        # ① 情绪反转: 与前一个片段情绪差≥2
        if i > 0:
            prev_emo = sorted_clips[i-1].get('emotion', 3)
            curr_emo = c.get('emotion', 3)
            if abs(curr_emo - prev_emo) >= 2:
                score += 30

        # ② 悬念词
        if any(w in desc for w in suspense_words):
            score += 25

        # ③ 场景切换
        if 'scene_change' in c.get('scene_types', []) or \
           any(st in c.get('scene_types', []) for st in ['对峙', '冲突', '战斗', '惊恐', '诡异']):
            score += 20

        # ④ 高情绪 + 新场景
        if c.get('emotion', 3) >= 4 and len(c.get('scene_types', [])) >= 1:
            score += 15

        if score >= 30:  # 阈值
            hook_candidates.append({
                "index": i, "score": score,
                "clip": c, "ep": c.get('ep'), "time": c.get('time'),
                "type": _hook_type_label(score, c),
            })

    if not hook_candidates:
        return [{"episode": 1, "clips": sorted_clips,
                 "duration": len(sorted_clips) * 7, "hook": "全剧精华"}]

    # 按分数排序取断点
    hook_candidates.sort(key=lambda h: (-h['score'], h['index']))
    min_clips, max_clips = 5, 8
    episodes = []
    used = set()
    ep_num = 1

    for hook in hook_candidates:
        if hook['index'] in used: continue
        start = max(0, hook['index'] - max_clips // 2)
        end = min(len(sorted_clips), hook['index'] + max_clips // 2)
        if any(i in used for i in range(start, end)): continue
        ep_clips = sorted_clips[start:end]
        if len(ep_clips) < min_clips: continue
        for i in range(start, end): used.add(i)
        episodes.append({
            "episode": ep_num,
            "clips": ep_clips,
            "duration": len(ep_clips) * 7,
            "hook": f"[{hook['type']}] {hook['clip'].get('desc_clean','')[:30]}",
            "hook_time": f"EP{hook['ep']} {hook['time']}s",
        })
        ep_num += 1

    remaining = [sorted_clips[i] for i in range(len(sorted_clips)) if i not in used]
    if remaining and len(remaining) >= 3:
        episodes.append({"episode": ep_num, "clips": remaining,
                         "duration": len(remaining) * 7, "hook": "后续剧情"})
    return episodes


def _hook_type_label(score, clip):
    if score >= 60: return "情绪反转+悬念"
    elif score >= 45: return "悬念节点"
    elif score >= 30: return "场景突变"
    return "情节节点"


# ═══════════════════════════════════════════
# 5.8 叙事功能标签 — 解决「评分只按情绪，不管叙事位置」
# ═══════════════════════════════════════════

# V2 event → 叙事功能映射 (基于两份提案的场景功能分类)
EVENT_TO_NARRATIVE = {
    # 建置类
    "葬礼": "setup", "仪式": "setup", "日常": "setup",
    "悲伤": "setup", "跪地": "setup",
    # 激励事件
    "冲突": "inciting", "威胁": "inciting", "背叛": "inciting",
    # 上升动作
    "对峙": "rising", "战斗": "rising", "误会": "rising",
    "出征": "rising",
    # 高潮
    "反转": "climax", "表白": "climax", "复仇宣言": "climax",
    "打斗": "climax",
    # 钩子
    "惊恐": "hook", "诡异": "hook", "悬念": "hook",
    # 过渡
    "回忆": "transition", "其他": "transition",
}

# 叙事功能的剪辑权重 (高潮>钩子>激励>上升>建置>过渡)
NARRATIVE_WEIGHT = {
    "climax": 2.0, "hook": 1.8, "inciting": 1.5,
    "rising": 1.2, "setup": 1.0, "transition": 0.3,
}

# 短剧黄金节拍: 每个功能在成片中的理想占比
NARRATIVE_BEAT_RATIO = {
    "hook": 0.15,      # 前15%是钩子
    "inciting": 0.15,  # 激励事件
    "rising": 0.30,    # 冲突升级(占最多)
    "climax": 0.25,    # 高潮
    "setup": 0.10,     # 建置
    "transition": 0.05, # 过渡
}


def tag_narrative_function(clip):
    """给单个片段打叙事功能标签"""
    events = clip.get('scene_types', [])
    if not events:
        return "rising"  # 默认

    # 按优先级找匹配
    for event in events:
        func = EVENT_TO_NARRATIVE.get(event)
        if func:
            return func
    return "rising"


def enforce_narrative_diversity(ranked_clips, top_n=8):
    """
    强制叙事功能多样性 + 集数范围多样性。
    基于 NARRATIVE_BEAT_RATIO 分配每种功能的名额，
    同时确保片段覆盖全剧集数范围（前中后期）。
    """
    # 先给每个片段打标签
    for c in ranked_clips:
        clip = c.get('clip', c)
        c['_narrative_func'] = tag_narrative_function(clip)
        c['_ep'] = int(clip.get('ep', '0') or 0)

    all_eps = sorted(set(c['_ep'] for c in ranked_clips if c['_ep'] > 0))
    if not all_eps:
        return ranked_clips[:top_n]
    max_ep = max(all_eps)

    # 计算每种功能的目标数量
    target_counts = {}
    for func, ratio in NARRATIVE_BEAT_RATIO.items():
        target_counts[func] = max(1, round(top_n * ratio))

    # 按功能分组，组内按分数排序
    by_func = {}
    for c in ranked_clips:
        func = c.get('_narrative_func', 'rising')
        by_func.setdefault(func, []).append(c)

    # 按功能权重排序(功能本身的重要性)
    func_order = sorted(NARRATIVE_WEIGHT.keys(), key=lambda f: -NARRATIVE_WEIGHT[f])

    result = []
    func_used = {}

    # 第1轮: 每种功能选1个最高分
    for func in func_order:
        if func in by_func and func_used.get(func, 0) < 1:
            best = by_func[func][0]
            result.append(best)
            func_used[func] = func_used.get(func, 0) + 1

    # 第2轮: 按目标数量补充
    for func in func_order:
        need = target_counts.get(func, 1) - func_used.get(func, 0)
        candidates = by_func.get(func, [])
        for c in candidates:
            if need <= 0: break
            if c not in result:
                result.append(c)
                func_used[func] = func_used.get(func, 0) + 1
                need -= 1

    # 第3轮: 不满top_n的用剩余高分填满
    if len(result) < top_n:
        for c in ranked_clips:
            if c not in result:
                result.append(c)
            if len(result) >= top_n:
                break

    # 第4轮: 集数范围多样性 — 确保覆盖全剧
    _enforce_episode_spread(result, ranked_clips, top_n, max_ep)

    # 按节拍顺序排列(hook→setup→inciting→rising→climax→transition)
    beat_order = {"hook": 1, "setup": 2, "inciting": 3, "rising": 4, "climax": 5, "transition": 6}
    result.sort(key=lambda c: beat_order.get(c.get('_narrative_func', 'rising'), 9))

    # 确保每种可用节拍至少保留1个（climax不会被slice截掉）
    # 始终执行此逻辑，因为调用方可能用更小的 top_n 做最终切片
    available_funcs = set(c.get('_narrative_func', 'rising') for c in result)
    if len(available_funcs) <= top_n:
        kept = []
        funcs_seen = set()
        # 先每种功能保留1个
        for c in result:
            func = c.get('_narrative_func', 'rising')
            if func not in funcs_seen:
                kept.append(c)
                funcs_seen.add(func)
        # 剩余名额按已有顺序补充
        remaining = top_n - len(kept)
        if remaining > 0:
            for c in result:
                if c not in kept:
                    kept.append(c)
                    if len(kept) >= top_n:
                        break
        # 恢复节拍顺序
        kept.sort(key=lambda c: beat_order.get(c.get('_narrative_func', 'rising'), 9))
        result = kept

    return result[:top_n]


def _enforce_episode_spread(result, pool, top_n, max_ep):
    """确保选中片段覆盖全剧集数范围"""
    if max_ep <= 50:
        return  # 短剧不需要强制

    # 分成3段: 前期(0-33%), 中期(33-66%), 后期(66-100%)
    boundaries = [max_ep * 0.33, max_ep * 0.66]
    segments = [
        ("前期", 1, int(boundaries[0])),
        ("中期", int(boundaries[0]) + 1, int(boundaries[1])),
        ("后期", int(boundaries[1]) + 1, max_ep),
    ]

    for seg_name, lo, hi in segments:
        has_clip = any(
            c['_ep'] >= lo and c['_ep'] <= hi
            for c in result
        )
        if has_clip:
            continue

        # 从pool中找到这个区段的最佳替代片段
        best_alt = None
        for c in pool:
            ep = c['_ep']
            if ep < lo or ep > hi:
                continue
            if c in result:
                continue
            best_alt = c
            break

        if best_alt is None:
            continue

        # 找到result中可替换的片段: 同叙事功能 + 所在区段有多个代表
        alt_func = best_alt.get('_narrative_func', 'rising')
        for ci in result:
            if ci.get('_narrative_func') != alt_func:
                continue
            ci_ep = ci['_ep']
            # 检查ci所在区段是否还有别的代表
            ci_seg = None
            for sn, slo, shi in segments:
                if ci_ep >= slo and ci_ep <= shi:
                    ci_seg = sn
                    break
            same_seg_count = sum(
                1 for c2 in result
                if c2['_ep'] >= slo and c2['_ep'] <= shi
            )
            if same_seg_count >= 2:
                result.remove(ci)
                result.append(best_alt)
                break


# ═══════════════════════════════════════════
# 5.9 主角检测 — 不靠人脸识别，靠叙事证据
# ═══════════════════════════════════════════

def detect_protagonist(clips, whisper_data=None):
    """
    从审片数据+Whisper对白推断主角。
    不依赖人脸识别，纯粹靠叙事证据。

    whisper_data: 可选, Whisper转录的对话列表 [{"start":0, "text":"..."}, ...]
                  如果提供, 会从中提取角色名并交叉验证。
    """
    if not clips: return None

    # ── 从V2视觉数据统计人物 ──
    char_episodes, char_emotions, char_scenes = {}, {}, {}
    for c in clips:
        ep = c.get('ep', '?')
        emo = c.get('emotion', 3)
        scenes = c.get('scene_types', [])
        chars_field = c.get('_v2', {}).get('chars', '') if '_v2' in c else c.get('desc_clean', '')
        if not chars_field: continue
        char_list = [x.strip() for x in chars_field.split(';') if x.strip()]
        if not char_list: char_list = [chars_field[:20]]
        for ch in char_list:
            ch = ch[:30]
            char_episodes.setdefault(ch, set()).add(ep)
            char_emotions.setdefault(ch, []).append(emo)
            char_scenes.setdefault(ch, set()).update(scenes)

    if not char_episodes: return None

    max_eps = max(len(eps) for eps in char_episodes.values())
    vis_scores = {}
    for ch, eps in char_episodes.items():
        ep_s = len(eps) / max(1, max_eps) * 30
        emos = char_emotions.get(ch, [3])
        emo_s = min(30, (sum(emos) / max(1, len(emos)) - 2) * 10)
        scene_s = min(20, len(char_scenes.get(ch, set())) * 4)
        vis_scores[ch] = round(ep_s + emo_s + scene_s, 1)

    best_vis, best_vis_score = max(vis_scores.items(), key=lambda x: x[1])

    # ── 从Whisper提取角色名(如果有) ──
    char_name = best_vis  # 默认用视觉描述
    if whisper_data:
        import re
        all_text = ' '.join(w.get('text', '') for w in whisper_data)
        name_pattern = re.findall(r'([\w一-鿿]{1,3})(?:说|问|喊|道|叫|曰)', all_text)
        if name_pattern:
            from collections import Counter
            # 过滤泛化词和代词
            stop_names = {'老人','男子','女子','有人','谁','什么','怎么',
                         '你','我','他','她','它','你们','我们','他们',
                         '这','那','哪','啥','咋','这样','那样'}
            filtered = [n for n in name_pattern if n not in stop_names and len(n) >= 2]
            if filtered:
                name_count = Counter(filtered)
                char_name = name_count.most_common(1)[0][0]

    protag_clips = []
    for c in clips:
        cf = c.get('_v2', {}).get('chars', '') if '_v2' in c else c.get('desc_clean', '')
        if best_vis[:15] in cf: protag_clips.append(c)

    return {
        "id": char_name,                     # 角色名(如果有Whisper)或视觉描述
        "visual_id": best_vis,               # V2视觉标识
        "score": best_vis_score,
        "all_characters": sorted(vis_scores.items(), key=lambda x: -x[1])[:5],
        "protagonist_clips": protag_clips,
        "evidence": {
            "episodes": len(char_episodes[best_vis]),
            "avg_emotion": round(sum(char_emotions[best_vis]) / max(1, len(char_emotions[best_vis])), 1),
            "scene_types": list(char_scenes[best_vis])[:5],
        }
    }


def filter_by_protagonist(clips, protagonist_info, keep_ratio=0.7):
    """主角视角过滤: 保留主角片段 + keep_ratio比例的其他片段"""
    if not protagonist_info or not protagonist_info.get('protagonist_clips'):
        return clips
    protag_ids = set(id(c) for c in protagonist_info['protagonist_clips'])
    other = [c for c in clips if id(c) not in protag_ids]
    import random; random.shuffle(other)
    result = list(protagonist_info['protagonist_clips']) + other[:int(len(other) * keep_ratio)]
    result.sort(key=lambda c: (int(c.get('ep','0') or 0), c.get('time', 0)))
    return result


# ═══════════════════════════════════════════
# 6. AI 二次确认（千问语义判断，替代关键词误判）
# ═══════════════════════════════════════════

def ai_confirm_genre(analysis_file, keyword_result, sample_lines=30):
    """
    用千问 API 对关键词识别结果做二次确认。
    输入: 审片文件 + 关键词初筛结果
    输出: {"confirmed_type": "horror", "confidence": 0.92, "reason": "..."}
    如果 API 不可用，直接返回关键词结果。
    """
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key="sk-63bcf94c95f8416b974b3902e6581888",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    except Exception:
        return {"confirmed_type": keyword_result['primary_genre'],
                "confidence": keyword_result['confidence'] / 5.0,
                "reason": "关键词识别(API不可用)", "source": "keyword_only"}

    # 读取采样行
    lines = []
    with open(analysis_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                parts = line.split(' ', 2)
                desc = parts[2] if len(parts) > 2 else line
                lines.append(desc)
    if len(lines) > sample_lines:
        import random
        sample = random.sample(lines, sample_lines)
    else:
        sample = lines

    # 构建 prompt
    kw_scores = keyword_result.get('all_scores', {})
    kw_summary = "\n".join([
        f"  {detail['label']}: {detail['score']:.3f} (strong匹配: {detail['strong_coverage']})"
        for gid, detail in kw_scores.items() if detail['score'] > 0
    ])

    prompt = f"""你是短剧类型识别专家。基于以下画面描述，判断这部短剧的题材类型。

【关键词初筛结果】
{kw_summary}

【画面描述采样】
{chr(10).join(sample[:sample_lines])}

类型候选（7类）：
- horror: 恐怖悬疑（鬼/诡异/葬礼/阴森/尸/棺/祭坛）
- action_historical: 古装权谋动作（打斗/剑/朝堂/权谋/牢狱/战斗）
- romance: 甜宠言情（吻/拥抱/甜蜜/宠/心动/恋爱）
- comedy: 喜剧搞笑（搞笑/滑稽/夸张/出糗/耍宝）
- mystery: 悬疑推理（谜/真相/追踪/秘密/案件）
- revenge: 复仇打脸（复仇/背叛/羞辱/逆袭/打脸）
- family: 家庭伦理（婆媳/小三/出轨/婚姻/赡养/家产）
- urban: 都市逆袭（创业/暴富/职场/资本/上位）

规则：
1. 只看最主要题材，忽略次要元素
2. 优先相信画面高频场景（比如一直出现葬礼→恐怖；一直出现打斗→古装权谋）
3. 输出严格JSON: {{"confirmed_type":"xxx","confidence":0.XX,"reason":"简要理由30字内"}}
"""
    try:
        resp = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=256
        )
        content = resp.choices[0].message.content
        # 加固JSON解析
        import re as _re
        for pat in [r'```json\s*([\s\S]*?)\s*```', r'```\s*([\s\S]*?)\s*```', r'(\{[\s\S]*\})']:
            m = _re.search(pat, content)
            if m:
                try:
                    ai_result = json.loads(m.group(1))
                    ai_result["source"] = "qwen_ai"
                    return ai_result
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        pass

    # Fallback: 返回关键词结果
    return {"confirmed_type": keyword_result['primary_genre'],
            "confidence": keyword_result['confidence'] / 5.0,
            "reason": "AI不可用,回退关键词", "source": "keyword_fallback"}


# ═══════════════════════════════════════════
# 7. 更新 GenreDetector: 支持两段式识别
# ═══════════════════════════════════════════

def _genre_detect_two_pass(analysis_file, use_ai=True):
    """两段式识别: 关键词初筛 → AI精判(可选)"""
    detector = GenreDetector()
    kw_result = detector.analyze(analysis_file)

    if use_ai:
        ai_result = ai_confirm_genre(analysis_file, kw_result)
        confirmed = ai_result.get('confirmed_type', kw_result['primary_genre'])
    else:
        confirmed = kw_result['primary_genre']
        ai_result = {"source": "keyword_only"}

    config = detector.get_weights(analysis_file)
    edit_cfg = EDIT_CONFIGS.get(confirmed, EDIT_CONFIGS.get('action_historical', {}))

    return {
        "keyword_pass": kw_result,
        "ai_pass": ai_result if use_ai else None,
        "final_genre": confirmed,
        "final_label": GENRE_FINGERPRINTS.get(confirmed, {}).get('label', '未知'),
        "weights": config['weights'],
        "scene_bonus": config.get('scene_bonus', {}),
        "edit_config": edit_cfg,
        "confidence": ai_result.get('confidence', config.get('confidence', 0.5)) if use_ai else config.get('confidence', 0.5),
    }


# ═══════════════════════════════════════════
# 8. 更新一键入口: 输出完整剪辑建议
# ═══════════════════════════════════════════

def analyze_and_rank(analysis_file, n_passes=5, use_ai=True, verbose=True):
    """
    输入审片文件 → 两段式类型识别 → 多轮比对 → 剪辑建议。
    """
    # 加载数据
    clips = []
    with open(analysis_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            parts = line.split(' ', 2)
            if len(parts) < 3: continue
            ep_str = parts[0].replace('ep', '')
            time_str = parts[1].replace('s', '')
            desc = parts[2]
            parsed = parse_vision_line(desc)
            parsed["ep"] = ep_str
            parsed["time"] = int(time_str) if time_str.isdigit() else 0
            clips.append(parsed)

    if verbose:
        print(f"加载 {len(clips)} 条审片记录")

    # 步骤1: 两段式类型识别
    genre_info = _genre_detect_two_pass(analysis_file, use_ai=use_ai)
    kw = genre_info['keyword_pass']

    if verbose:
        ai_tag = "[AI确认]" if genre_info.get('ai_pass', {}).get('source') == 'qwen_ai' else "[关键词]"
        print(f"\n{ai_tag} 类型: {genre_info['final_label']} "
              f"(置信度 {genre_info['confidence']})")
        if genre_info.get('ai_pass'):
            reason = genre_info['ai_pass'].get('reason', '')
            if reason:
                print(f"  AI理由: {reason}")
        # 分数详情
        for gid, detail in kw['all_scores'].items():
            if detail['score'] > 0:
                strong_kw = [f"{k}({v})" for k, v in detail['top_strong'][:3]]
                anti_kw = [f"{k}({v})" for k, v in detail.get('top_anti', [])[:2]]
                anti_str = f"  anti: {', '.join(anti_kw)}" if anti_kw else ""
                print(f"  {detail['label']}: {detail['score']:.3f} "
                      f"strong={detail['strong_coverage']} weak={detail['weak_coverage']}"
                      f"{anti_str} → {', '.join(strong_kw)}")

    # 步骤2: 多轮比对
    if verbose:
        print(f"\n多轮比对评分 (n={n_passes})...")
    genre_config = {"weights": genre_info['weights'], "scene_bonus": genre_info['scene_bonus']}
    result = multi_pass_rank(clips, genre_config, n_passes=n_passes)

    if verbose:
        print(f"\n共识片段 (Top {len(result['consensus'])} 个, 所有轮次均入选):")
        for i, c in enumerate(result['consensus'][:15]):
            clip = c['clip']
            scenes = '/'.join(clip.get('scene_types', [])[:3]) or '-'
            print(f"  {i+1:2d}. {c['id']} avg_rank={c['mean_rank']:.1f} "
                  f"std={c['std_rank']:.1f} emo={clip['emotion']} "
                  f"scenes=[{scenes}]")

        if result['divergences']:
            print(f"\n分歧片段 (排名波动大, 建议人工确认):")
            for d in result['divergences'][:5]:
                print(f"  {d['id']} ranks={d['ranks']} std={d['std_rank']:.1f}")

    # 步骤3: 剪辑建议
    edit_cfg = genre_info['edit_config']
    if verbose and edit_cfg:
        print(f"\n剪辑建议 ({genre_info['final_label']}):")
        print(f"  调色: {edit_cfg.get('color_grade', '默认')}")
        print(f"  BGM:  {edit_cfg.get('bgm_style', '默认')}")
        print(f"  转场: {edit_cfg.get('transition', 'hard_cut')}")
        print(f"  节奏: {edit_cfg.get('pacing', '默认')}")
        print(f"  片段: {edit_cfg.get('clip_dur', (3,8))[0]}-{edit_cfg.get('clip_dur', (3,8))[1]}s")

    return {
        "genre": genre_info,
        "ranking": result,
        "clips": clips,
        "edit_config": edit_cfg,
    }


# ═══════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════
if __name__ == "__main__":
    import sys

    # 测试文件
    test_files = {
        "终宋": r"E:\BaiduNetdiskDownload\05.终宋（76集）陈外＆王涵\clips_promo\scan_76\analysis.txt",
        "三尸语": r"E:\BaiduNetdiskDownload\三尸语_剪辑工作台\全剧分析.txt",
    }

    for name, fpath in test_files.items():
        if not os.path.exists(fpath):
            print(f"{name}: 审片文件不存在, 跳过\n")
            continue
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
        result = analyze_and_rank(fpath, n_passes=5, verbose=True)
