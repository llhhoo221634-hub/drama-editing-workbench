# Changelog

## 2026-05-22 #4 — 趋势标签架构 + 分位数熔断（76集全集）

### 13. `multi_frame_sample.py` — Prompt 改为趋势标签版

**改了啥**: 删掉 emo/action_level/vq 数字评分要求，改为输出 `action_direction`(增强/持续/静止/减弱) 和 `emotion_trend`(上升/稳定/下降/爆发) 两个趋势标签 JSON 字段。加 event 互斥约束（无肢体冲突→禁止标"冲突"）。三帧多图 Image List 发送。

**为什么要改**: 前三轮验证证明 VL 不擅长绝对评分（膨胀到93%挤在3、偏移只平移不恢复），但擅长定性趋势描述。架构分离：VL做Narrator，V3做Judge。

### 14. `build_analysis_v3.py` — 趋势组合→评分 + 两阶段分位数熔断

**改了啥**: 
- Pass 1: 趋势标签组合映射原始分数：(爆发,增强)=5 (上升,增强)=4 (稳定,增强)=3... + event_subtype高能词库(抓扯/持械/怒吼)+1 + 正脸/字幕加分
- Pass 2: 全剧分位数熔断 top-5%→5, top-5-15%→4, top-15-35%→3, top-35-60%→2, bottom-40%→1。熔断后重算cut_role。

**为什么要改**: 原始趋势评分 hook=5 占 57%(260/457帧)，偏高。分位数熔断后压到 5%(23帧)，恢复长尾分布。不同剧集自动适配。

### 15. `multi_frame_sample.py` — 三帧邻截 ±1.5s + Image List

**改了啥**: 每个关键帧额外截取 ts-1.5s 和 ts+1.5s 邻帧，三帧通过兼容接口 Image List 平铺发送（非拼图）。

**为什么要改**: 单帧无法捕捉动作/情绪变化。三帧 ≈ 最廉价的"伪视频"，已能解决70%的动作理解问题。兼容接口原生支持多图数组，无需切原生SDK。

---

## 2026-05-21 #3 — CSV 审片 + afade 音频平滑 + V3 标定工具

### 10. `run_promo_molecular.py` — assemble_molecular() 音频 afade 平滑

**改了啥**: 片段 concat 前对每段音频加 `afade=t=in:d=0.2:curve=tri,afade=t=out:st={dur-0.2}:d=0.2:curve=tri`，淡入淡出 0.2s。

**为什么要改**: 多段不同时空的画面直接拼贴时，音频突变（嘈杂打斗 → 安静室内）会产生"爆音"和"断层感"。afade 在拼接点做 0.2s 交叉淡入淡出，成本极低但听感提升明显。

### 11. `run_promo_molecular.py` — CSV 审片机制

**改了啥**: 新增 `--export-csv`（dry-run 时导出 `review_<type>.csv`）和 `--from-csv <path>`（从人工编辑后的 CSV 读回确认片段生成视频）。

**CSV 字段**: `keep(Y/N)`, `ep`, `time(s)`, `suggested_dur(s)`, `cut_role`, `best_cut`, `event`, `subtype`, `emo`, `visual_quality`, `face_quality`, `hook_value`, `promo_value`, `desc`

**为什么要改**: 全自动选片无法保证叙事连贯性，需要在"粗剪→审片→精剪"之间加人工确认环节。CSV 是零依赖桥接方案，人标记 keep 列后直接喂回脚本。

### 12. `calibrate_v3.py` — V3 评分盲标校准工具（新增）

**功能**: `generate` 从 V3 可用帧随机抽 N 帧生成盲标 CSV（不含 V3 分数），`analyze` 读回标注后计算混淆矩阵和假阳性率。

**为什么要加**: V3 评分公式目前全是规则推导，没有 ground truth 验证。在没有运营/投放专家的情况下，开发者自己标 30 帧是最低成本的标定方式。

---

## 2026-05-21 #2 — V3 数据源切换 + 统一切割公式

### 6. `build_analysis_v3.py` — `enrich()` 的 `usable` 计算 bug 修复

**改了啥**: `usable = reject_reason == "无"` — 不再 AND V2 原始 `usable` 标志。

**为什么要改**: `infer_reject()` 可能清除 V2 的拒绝原因（如"纯文字"→"无"），但原公式保留 V2 的 `usable=False`，造成 `"usable":false,"reject_reason":"无"` 的矛盾状态。修复后 10 集可用帧从 70→71。

### 7. `run_promo_molecular.py` — 数据源从 V2 切换到 V3

**改了啥**: `ANALYSIS_FILE` 从 `_project["analysis_v2"]` 改为 `_project["analysis_v3"]`。

**为什么要改**: 分子宣发一直读 V2，V3 里精心计算的 `promo_value`/`hook_value`/`best_cut`/`pre_roll`/`suggested_duration` 等决策字段从未被使用，形成"数据断层"。切换后分子筛选和切割直接消费 V3 决策层。

### 8. `edit_utils.py` — `parse_vision_line()` 新增 7 个 V3 决策字段提取

**改了啥**: 在 V2/V3 JSON 解析分支增加顶层提取：`promo_value`、`hook_value`、`cut_role`、`best_cut`、`pre_roll`、`post_roll`、`suggested_duration`。

**为什么要改**: 和上次音频字段遗漏一样的问题 — V3 字段在 JSON 顶层但没被提取到 dict 顶层，下游代码取到默认值。不补这个缺口，切换 V3 数据源毫无意义。

### 9. `run_promo_molecular.py` — `cut_molecular_clips()` 统一切割公式

**改了啥**: 用 `start = max(0, t - pre_roll)` + `dur = max(min_dur, min(suggested_dur, max_dur))` 替代原有的 6 类分子 if/elif 硬编码盲切。

**为什么要改**: V3 已经在 per-frame 粒度给出了 `pre_roll`（从哪个方向入点）和 `suggested_duration`（建议切多长），分子类型提供 `clip_dur` 上下限做节奏天花板。两者叠加比旧代码的"盲猜偏移"精准。效果：4/6 类分子视频时长更贴近目标值。

---

## 2026-05-21 #1 — 质量判定 & 音频字段链路修复

### 1. `multi_frame_sample.py` — 质量探测"低质量"误杀修复

**改了啥**: `frame_quality_probe()` 里 `stdev <= 0.8` 不再无条件标记"低质量"，改为仅当同时满足 `mean <= 20`（极暗）或 `mean >= 235`（极亮）时才触发。

**为什么要改**: 之前 `stdev <= 0.8` 对任何低对比帧（含正常暗调/夜戏画面）都会判为"低质量"，导致大量可用的夜景、室内暗戏帧被误杀。

### 2. `multi_frame_sample.py` — "纯文字"判定加字幕长度门槛

**改了啥**: `infer_quality_fields()` 中"纯文字"关键词匹配改为仅当 `subtitle_text` 长度 >= 12 字符时才触发。

**为什么要改**: 之前画面中出现任何"字幕卡"关键词就会判纯文字丢弃，但大部分宣发可用帧都有短字幕（如"您要的"、"杀"），这些帧画面主体是人物而非文字卡。

### 3. `build_analysis_v3.py` — V3 reject 规则与 V2 对齐

**改了啥**: `infer_reject()` 三处调整：
- 已标记"纯文字"的帧，若 `visual_quality >= 4` 且字幕 < 12 字符且非空镜，恢复为可用
- `event_conf == "模糊"` 仅在 `visual_quality <= 2` 时才判模糊丢弃
- `shot == "空镜"` 仅在 `visual_quality <= 2` 时才判空镜丢弃

**为什么要改**: V3 的 reject 规则比 V2 更严，造成部分 V2 已判定可用的帧到 V3 又被重新丢弃，管道口径不一致。

### 4. `run_promo_molecular.py` — 分子筛选"模糊"拦截过于激进

**改了啥**: `_molecular_filter_pass()` 中 `event_conf == "模糊"` 改为 `event_conf == "模糊" and visual_quality <= 2` 才拦截。

**为什么要改**: Vision API 返回的 `event_conf="模糊"` 只是说事件主体不清，不代表画面不可用。高画质（visual_quality >= 3）但有模糊事件的帧仍然适合宣发（如背影、空镜过渡）。

### 5. `edit_utils.py` — `parse_vision_line()` 遗漏音频字段提取（本次最关键修复）

**改了啥**: 在 V2 JSON 解析分支增加 6 个音频增强字段的顶层提取：
- `audio_energy` (音频能量 1-5)
- `speech_density` (对白密度 1-5)
- `has_speech_peak` (是否有语音爆发点)
- `beat_nearby` (是否靠近节奏点/边界)
- `transcript_excerpt` (转录摘录)
- `dialogue_anchor` (对话锚点类型)

**为什么要改**: `multi_frame_sample.py` 通过 `summarize_audio_window()` 写入了这些字段到 `analysis_v2.txt`，但 `parse_vision_line()` 没把它们提取到顶层 dict。导致：
- `molecular_score()` 对所有片段取默认 `audio_energy=1`，评分失真
- `_is_legacy_data()` 误判全部数据为 legacy，触发三级降级过滤
- `quote_rhythm` 类型始终报 `no_transcript` / `no_rhythm_anchor` 警告
- 实际效果：修复前 `hook_clash` 评分 144-167，修复后 168-212（V2 片段音频加分生效）
