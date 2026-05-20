---
name: videocut:短剧宣发
description: 短剧全流程剪辑工作台。自动管线(V2采样→V3决策→分子宣发) + 手动剪辑(切割/合并/混剪/包装/导出)。触发词：剪辑、切割、合并、混剪、宣发、预告片、做视频、审片、分子宣发、V3
---

# 短剧全流程剪辑工作台

> 自动管线 · 手动剪辑 · 方法论 · 专业工具

## 意图路由

| 用户说 | 跳转 |
|--------|------|
| 做宣发/预告片/推广视频/分子宣发/6类素材 | → §A.4 分子宣发生成 |
| 多帧采样/审片数据/V2分析/跑采样 | → §A.2 多帧采样 |
| V3升级/剪辑决策/升级数据 | → §A.3 V3决策升级 |
| 类型识别/方向推荐/8方向/管线 | → §A.5 全自动管线 |
| 一键出片/全自动/批量流水线 | → §A.6 一键出片 |
| 切/截/剪出某段 | → §B.1 片段切割 |
| 合并/拼接/接起来 | → §B.2 合并拼接 |
| 混剪/画中画/快放/慢放/倒放 | → §B.3 创意混剪 |
| 批量加片头片尾/字幕/水印 | → §B.4 批量包装 |
| 提取音频/转格式/裁剪画面/缩放 | → §B.5 音画处理 |
| 导出Premiere/导出AE/EDL/母版 | → §B.6 Premiere/AE导出 |
| 爆款公式/钩子/节奏/去重 | → §C 方法论速查 |
| 审片/选段/爽点/虐点 | → §C.2 审片框架 |
| 封面/降噪/声音处理/调色/字幕样式 | → §D 专业工具 |

---

## 公共环境（参数化，不绑定具体剧）

```bash
# 从 config.json 读取路径（每个项目独立配置）
FFMPEG=$(node -e "console.log(require('./config.json').ffmpeg)")
FFPROBE=$(node -e "console.log(require('./config.json').ffprobe)")

# 项目参数（由 CLAUDE.md 或 config.json 提供）
MEDIA_DIR=<原片目录>
RESOLUTION=<ffprobe 自动探测，如 720x1280>
EPISODES=<总集数>
BGM=<背景音乐路径>
```

**首次接触新项目时**：
1. `ffprobe` 探测分辨率和帧率
2. `ls` 统计集数
3. 读取项目 CLAUDE.md 获取高冲突集数、BGM 等信息

---

## §A 自动管线（核心能力）

### A.1 管线总览

```
multi_frame_sample.py（V2采样+质量标记）
    ↓
build_analysis_v3.py（离线升级为剪辑决策数据）
    ↓
run_promo_molecular.py（6类分子宣发自动生成）
    ↓
genre_engine.py + auto_script_gen.py（类型识别+全自动管线）
```

### A.2 多帧采样（V2数据生成）

```bash
# 场景检测 + 多帧采样 + 千问VL结构化识图
python multi_frame_sample.py --eps 1,7,29 --workers 1

# 断点续跑（跳过已完成集数）
python multi_frame_sample.py --resume --workers 2
```

**输出**：`analysis_v2.txt`
**V2字段**：usable, reject_reason, visual_quality, face_quality, action_level, event_subtype, timestamp, dialogue_visible, subtitle_text

**过滤能力**：自动标记白屏、黑屏、片头、纯文字、模糊帧为 usable=false

### A.3 V3 剪辑决策升级

```bash
python build_analysis_v3.py
```

**输入**：analysis_v2.txt
**输出**：
- `analysis_v3.txt` — 增加剪辑决策字段
- `analysis_v3_rejects.txt` — 不可用帧清单

**V3新增字段**：

| 字段 | 含义 | 取值 |
|------|------|------|
| promo_value | 宣发价值 | 1-5 |
| hook_value | 钩子强度 | 1-5 |
| emotion_value | 情绪价值 | 1-5 |
| action_value | 动作价值 | 1-5 |
| visual_value | 视觉价值 | 1-5 |
| conflict_side | 冲突类型 | 群体冲突/双人对峙/单人爆发/情绪崩溃/无 |
| cut_role | 叙事位置 | hook/climax/rise/setup/ending |
| best_cut | 最佳切点 | on_action/before_action/after_reaction |
| pre_roll | 前摇秒数 | 0.5-1.5 |
| post_roll | 后摇秒数 | 1.5-3.5 |
| suggested_duration | 建议时长 | 2.0-5.0 |

**核心价值**：回答"这个镜头能不能用、适合放哪里、截多长"。

### A.4 分子宣发生成（6类）

```bash
# 先预览选片（不切割）
python run_promo_molecular.py --dry-run --types hook_clash,suspense_hook

# 正式生成
python run_promo_molecular.py --types hook_clash,identity_twist,emotional_resonance,quote_rhythm,cinematic_beauty,suspense_hook
```

**6类分子**：

| 类型 | 说明 | 选片策略 |
|------|------|---------|
| hook_clash | 冲突钩子 | hook_value≥4, 高动作+高冲突 |
| identity_twist | 身份反转 | 有对话+身份相关事件 |
| emotional_resonance | 情感共鸣 | emotion_value≥4, 悲伤/哭泣 |
| quote_rhythm | 金句卡点 | 有字幕+高情绪 |
| cinematic_beauty | 光影美学 | visual_value≥4, 全景/特写 |
| suspense_hook | 悬念钩子 | hook_value≥3, 截断式结尾 |

**质量保障**：
- 自动过滤 usable=false、visual_quality<3、推测污染
- 生成 `qa_<type>.json` 质检报告
- (ep, time±5s) 去重，避免相邻帧重复

### A.5 全自动管线（genre_engine + auto_script_gen）

```bash
# 一键管线：类型识别 → 方向推荐 → 评分 → 切割 → 组装
python auto_script_gen.py pipeline <源视频目录>
```

**7步流程**：
1. 查找审片数据
2. 两段式类型识别（关键词→AI确认）
3. 推荐4个最优方向 + 可选全部8个
4. 方向预筛选 + 多轮比对评分
5. 用户确认片段（叙事功能多样性+时间加权）
6. 并行切割（4并发）
7. QA检测（Whisper音频质检）

**5种剧集类型**：恐怖悬疑、古装权谋、甜宠言情、喜剧搞笑、悬疑推理
**8大方向**：宣传片、爽点混剪、第一人称、第三人称、角色Cut、剧情分集、情绪向、二创解说

### A.6 一键出片

JSON 配置 → 一条命令 → 4阶段全自动：

```json
{
  "project": "项目名",
  "media_dir": "原片目录",
  "style": "cold_suspense",
  "bgm": "bgm.wav",
  "clips": [
    {"source": "第XX集.mp4", "start": 58, "dur": 6, "label": "标签"}
  ]
}
```

```bash
bash scripts/oneclick.sh oneclick.json
# 阶段1: 精确裁剪 → 阶段2: 特效+调色+音频 → 阶段3: xfade组装+BGM → 阶段4: 封面+多平台导出
```

---

## §B 手动剪辑（ffmpeg 命令模板）

> 完整命令细节见 REFERENCE.md

### B.1 片段切割

```bash
# 无损切割（秒级精度）
"$FFMPEG" -y -ss <开始秒> -t <时长秒> -i "源.mp4" -c copy output.mp4

# 精确切割（帧级精度）
"$FFMPEG" -y -ss <开始秒> -t <时长秒> -i "源.mp4" \
  -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k output.mp4
```

### B.2 合并拼接

```bash
# 无损拼接
echo "file 'clip1.mp4'" > concat.txt && echo "file 'clip2.mp4'" >> concat.txt
"$FFMPEG" -y -f concat -safe 0 -i concat.txt -c copy merged.mp4

# xfade 转场拼接（用脚本生成命令）
node scripts/xfade_gen.js <各片段时长>
```

### B.3 创意混剪

```bash
# 画中画（右下角 1/3 宽度）
"$FFMPEG" -y -i main.mp4 -i pip.mp4 \
  -filter_complex "[1:v]scale=iw/3:-1[pip];[0:v][pip]overlay=W-w-20:H-h-20" output.mp4

# 2倍速
"$FFMPEG" -y -i input.mp4 \
  -filter_complex "[0:v]setpts=0.5*PTS[v];[0:a]atempo=2[a]" \
  -map "[v]" -map "[a]" output.mp4

# 倒放
"$FFMPEG" -y -i input.mp4 -vf reverse -af areverse output.mp4
```

### B.4 批量包装

```bash
# 加片头片尾
echo -e "file 'intro.mp4'\nfile '正片.mp4'\nfile 'outro.mp4'" > pack.txt
"$FFMPEG" -y -f concat -safe 0 -i pack.txt -c copy output.mp4

# 烧录字幕（抖音标准样式）
"$FFMPEG" -y -i input.mp4 \
  -vf "subtitles='sub.srt':force_style='FontSize=26,FontName=Microsoft YaHei,Bold=1,PrimaryColour=&H0000deff,Outline=2.5,MarginV=60'" \
  -c:a copy -crf 21 output.mp4

# 水印
"$FFMPEG" -y -i input.mp4 -i logo.png -filter_complex "overlay=W-w-20:20" -c:a copy output.mp4
```

### B.5 音画处理

```bash
# 提取音频
"$FFMPEG" -y -i input.mp4 -vn -acodec pcm_s16le output.wav

# 替换音频
"$FFMPEG" -y -i video.mp4 -i new.mp3 -c:v copy -c:a aac -map 0:v -map 1:a -shortest output.mp4

# 横屏→竖屏
"$FFMPEG" -y -i input.mp4 -vf "crop=ih*9/16:ih,scale=1080:1920" output.mp4

# 混合BGM
"$FFMPEG" -y -i video.mp4 -i bgm.wav -filter_complex \
  "[1:a]volume=0.3,afade=t=out:st=57:d=3[bgm];[0:a][bgm]amix=inputs=2:duration=first[aout]" \
  -map 0:v -map "[aout]" -c:v copy output.mp4
```

### B.6 Premiere/AE 导出

**推荐方案：独立裁剪片段**（XML/EDL 在 Premiere 2025 + 中文路径下不可靠）

```bash
# 每个片段精确裁剪为独立 mp4，拖入 Premiere 即可用
"$FFMPEG" -y -ss <start> -t <dur> -i "源.mp4" \
  -vf "scale=1080:1920,fps=30" -crf 18 "clip_NN.mp4"
```

**母版格式**：
- DNxHR HQ (.mov) — Windows/Premiere 推荐
- ProRes 422 HQ (.mov) — Mac 推荐

---

## §C 方法论速查

### C.1 爆款结构：黄金 3-15-30 公式

```
[0-3秒]   极端冲突/悬念钩子 → 打破心理预期（≤12字强冲击台词）
[3-15秒]  高能混剪 → 3-5个最精彩画面快拼
[15-30秒] 情绪落差 → 甜→虐→爽的极端反差
[结尾]    悬崖效应留白 → 关键处戛然而止
```

### C.2 审片框架（五层递进）

| 层级 | 问题 | 标准 |
|------|------|------|
| 情节 | 推动主线吗？ | 有起承转合 |
| 事件 | 承载有效信息？ | 无脂肪镜头 |
| 冲突 | 有困兽结构？ | 角色被困，观众代入 |
| 钩子 | 忍不住想看下去？ | 反转前微表情、半句话 |
| 情绪 | 触发哪种情绪？ | 爽/虐/甜/惊，必须纯粹 |

### C.3 分子级拆解（6维度，1部剧出20+条不重复素材）

| 分子类型 | 拆法 |
|----------|------|
| 情感分子 | 每种情绪独立成片（打脸爽感版、心疼逆袭版） |
| 人物分子 | 同一场戏从不同角色视角切入 |
| 冲突分子 | 每场冲突独立提取为爆点 |
| 节奏分子 | 同一段剪 3s卡点 / 15s情绪 / 30s故事 |
| 悬念分子 | 反转前铺垫单独剪出 |
| 金句分子 | 经典台词 + 不同BGM = 新素材 |

### C.4 节奏铁律

- 单镜头 ≤ 3秒，高潮压缩至 0.5-1秒
- 每 15秒设一个信息爆点
- 高潮后留 1-2秒呼吸停顿
- 删掉所有脂肪镜头（空洞走路、无意义空镜、拖沓对白）

### C.5 去重六件套

| 手法 | 命令 |
|------|------|
| 掐头去尾+重排 | `-ss 2` + 片段打乱顺序 |
| 镜像翻转 | `-vf hflip` |
| 缩放重构 | 放大110%重新构图 |
| 分段变速 | 非对话0.95x，高潮1.0x |
| BGM全量置换 | amix替换原声 |
| 抽帧 | `fps=29` 替代 `fps=30` |

### C.6 付费卡点定位

| 卡点 | 位置 | 特征 |
|------|------|------|
| 卡一 | 第8-16集 | 第一个重大转折（身份揭露/被逼绝境） |
| 卡二 | 第25-30集 | 新危机/更大反转 |
| 卡三 | 第50集左右 | 终极冲突爆发 |

---

## §D 专业工具

### D.1 调色预设

```bash
# 甜宠暖调
-vf "eq=contrast=1.1:saturation=1.2,colorbalance=rs=0.08:bs=-0.10"

# 悬疑冷调
-vf "eq=contrast=1.15:brightness=-0.05:saturation=1.1,colorbalance=rs=-0.10:bs=0.12"

# 爽剧高饱和
-vf "eq=contrast=1.25:saturation=1.4:gamma=1.1,curves=preset=strong_contrast"
```

### D.2 音频精修链（6段式）

```bash
"$FFMPEG" -i input.mp4 -af \
  "highpass=f=70, \
   equalizer=f=200:t=q:w=1:g=-2, \
   equalizer=f=3000:t=q:w=1.5:g=3, \
   deesser=i=0.5:f=6500:s=0:m=0, \
   acompressor=threshold=-20dB:ratio=2.5:attack=5:release=50:makeup=3, \
   loudnorm=I=-16:TP=-1.5:LRA=11:linear=true" \
  -c:v copy output.mp4
```

| 步骤 | 滤镜 | 作用 |
|------|------|------|
| 1 | highpass=70 | 切除低频闷响 |
| 2 | EQ 200Hz -2dB | 减少浑浊 |
| 3 | EQ 3kHz +3dB | 提升人声清晰度 |
| 4 | deesser 6.5kHz | 消除齿音 |
| 5 | acompressor 2.5:1 | 压缩动态范围 |
| 6 | loudnorm -16 LUFS | 抖音标准响度 |

### D.3 封面生成

```bash
# 智能选帧
"$FFMPEG" -ss 3 -i input.mp4 -frames:v 1 -vf "thumbnail,scale=1080:1920" cover.jpg

# 叠加冲突文案
"$FFMPEG" -i cover.jpg -vf "drawtext=fontsize=72:fontcolor=white:\
  text='冲突词+数字+身份锚点':x=(w-text_w)/2:y=h-text_h-120:\
  box=1:boxcolor=black@0.6:boxborderw=10" cover_titled.jpg
```

### D.4 ffmpeg 特效命令

```bash
# 闪白转场（冲突爆发点）
-filter_complex "color=white:s=1080x1920:d=0.1[flash];[0:v][flash]overlay=enable='between(t,2,2.1)'"

# 震屏（打斗瞬间，±15px 0.3s）
-vf "crop=iw-30:ih-30:15+15*sin(2*PI*30*t)*between(t,1,1.3):15+15*cos(2*PI*30*t)*between(t,1,1.3),scale=1080:1920"

# 推进放大（zoom in 1.0→1.15）
-vf "scale=4320:7680,zoompan=z='min(max(zoom,pzoom)+0.0005,1.15)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30"
```

### D.5 高级字幕

```bash
# ASS 卡拉OK逐词高亮烧录
"$FFMPEG" -i video.mp4 -vf "ass=subtitle.ass" -crf 21 output.mp4

# 自动方案
pip install tiktok-karaoke-captions
karaoke-captions video.mp4 --output styled_output.mp4
```

---

## 工具链

| 工具 | 用途 |
|------|------|
| `multi_frame_sample.py` | 场景检测+多帧采样+V2质量标记 |
| `build_analysis_v3.py` | 离线V2→V3剪辑决策升级 |
| `run_promo_molecular.py` | 6类分子宣发生成+QA报告 |
| `genre_engine.py` | 类型识别+8方向推荐+预筛选 |
| `auto_script_gen.py` | AI全自动剪辑管线（Qwen→ffmpeg） |
| `edit_utils.py` | V2/V3解析、片段切割、QA、并行工具 |
| `scripts/xfade_gen.js` | 多片段xfade转场命令生成 |
| `scripts/edl_gen.js` | CMX3600 EDL生成（参考用） |
| `scripts/fcp_xml_gen.js` | FCP 7 XML生成（参考用） |
| `scripts/oneclick.sh` | JSON配置→一键出片 |
| `ffmpeg` / `ffprobe` | 所有视频处理核心 |
| `vision.js` / `vision_v2.js` | AI帧内容分析（Qwen-VL） |

## 外部API

| API | 用途 | 模型 |
|-----|------|------|
| 千问 DashScope | 帧分析 + 脚本生成 | qwen-vl-max / qwen-plus |
| Edge TTS | 中文配音 | zh-CN-YunyangNeural |
| Whisper | 语音识别QA | openai-whisper |

## 经验教训

- XML/EDL 在 Premiere 2025 + 中文路径下不可靠，改用独立裁剪片段
- xfade 组装前所有 clip 必须统一 fps=30
- acrossfade 要求音频格式一致（aformat 统一）
- vision.js 帧分析 prompt ≤15字效果最好
- 宣发字幕短文案优先（戏剧性短语 > 逐句对白）
- clip 总时长 40-50秒最适合抖音
- V2 推测污染词（似乎/可能/大概）必须过滤
- 分子宣发 (ep, time±5s) 去重避免相邻帧重复

---

> 完整 ffmpeg 命令细节、调色参数对比、母版格式表、ASS模板等见 `REFERENCE.md`
