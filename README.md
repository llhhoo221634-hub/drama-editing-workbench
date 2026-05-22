# 短剧宣发 · 全流程剪辑工作台

> 剧集切割 · 创意混剪 · 批量包装 · 宣发预告片 · 审片选段 · 封面生成 · 音频精修 · 批量流水线 · Premiere/AE 导出

## 能力总览

| 模块 | 内容 |
|------|------|
| **基础剪辑** (§1-§5) | 片段切割、合并拼接、创意混剪（画中画/变速/倒放）、批量包装（片头尾/字幕/水印）、音画处理（提取/替换/横竖转换/压缩） |
| **宣发制作** (§6) | 多帧采样→DashScope Vision 结构化识图→V3 决策升级→6类分子选片→timeline 导出→ffmpeg 组装成片 |
| **交付导出** (§7-§8) | XML/EDL交换格式生成器、独立裁剪片段（Premiere/AE 100%兼容） |
| **剪辑方法论** (§9-§10) | 黄金3-15-30爆款公式、7种情绪钩子模板、ffmpeg特效命令（闪白/震屏/变速/zoom）、去重六件套、音效设计、分子级审片拆解法 |
| **专业进阶** (§11) | 封面生成（智能选帧+标题叠加）、6段式音频精修链（降噪→EQ→齿音消除→压缩→响度标准化）、5阶段批量流水线 |

## 工具脚本

| 脚本 | 功能 |
|------|------|
| `multi_frame_sample.py` | 场景检测 + 多帧采样 + DashScope Vision 结构化识图，输出 V2 质量字段 |
| `build_analysis_v3.py` | 离线把 `analysis_v2.txt` 升级为剪辑决策型 `analysis_v3.txt` |
| `run_promo_molecular.py` | 6类分子宣发生成 + QA + timeline 导出 |
| `edit_utils.py` | V2/V3解析、音频窗口摘要、片段切割、QA、并行工具 |
| `genre_engine.py` | 类型识别、方向推荐、叙事功能标签、分集多样性 |
| `scripts/xfade_gen.js` | 自动生成多片段 xfade 转场组接的 ffmpeg 命令 |
| `scripts/edl_gen.js` | CMX3600 EDL 编辑决策表生成（Premiere 兼容） |
| `scripts/fcp_xml_gen.js` | FCP 7 XML 生成（含 ffprobe 源时长探测、Cross Dissolve 转场、URL 编码） |

## 环境要求

- **ffmpeg / ffprobe** — Windows winget 安装，路径自动探测
- **Python 3.9+** — `multi_frame_sample.py` / `build_analysis_v3.py` / `run_promo_molecular.py` 运行环境
- **DashScope API Key** — 千问 VL 视觉识别，在 `config.json` 的 `vision` 段配置

## 项目配置

当前仓库按“通用短剧引擎”方式工作，不再把源目录、输出目录、BGM、项目名写死在脚本里。

`config.json` 分为两层：
- **引擎配置**：`ffmpeg` / `ffprobe` / `dashscope_api_key` / `audio_analysis`
- **项目配置**：`project.project_name` / `media_dir` / `work_dir` / `analysis_v2` / `analysis_v3` / `analysis_fallback` / `frames_dir` / `molecular_dir` / `bgm`

关键点：
- 切换到另一部短剧时，优先改 `config.json` 的 `project` 段，而不是改脚本源码
- `episode_name_template` 用于适配不同命名，例如 `{ep}.mp4`、`{ep02}.mp4`、`第{ep}集.mp4`
- `episode_count` 可选；不填时脚本会尝试自动扫描 `media_dir` 中的剧集文件
- `vision` 段负责 Vision API 工程化配置：`model/base_url/api_key/max_short_edge/concurrency/max_retries/timeout`
- `audio_analysis` 段负责音频增强：`enabled/whisper_model/window_seconds/speech_peak_chars/energy_peak_threshold`
- 若 `analysis_v2` 缺失但 `analysis_fallback` 存在，`build_analysis_v3.py` 会回退读取 legacy 数据；`run_promo_molecular.py` 也会对 legacy 数据启用三级降级过滤（严格→放宽→评分兜底）

## 快速开始

```bash
# 1. 配置 ffmpeg / ffprobe / DashScope Key / project
cp config.example.json config.json

# 2. 在 config.json 的 project 段中填写：
#    - project_name
#    - media_dir
#    - work_dir
#    - analysis_v2 / analysis_v3 / analysis_fallback
#    - frames_dir / molecular_dir / bgm
#    - episode_name_template / episode_count

# 3. 多帧采样，生成 analysis_v2.txt
python multi_frame_sample.py --eps 1,7,29 --workers 1
python multi_frame_sample.py --resume --workers 2

# 4. 离线升级剪辑决策数据，生成 analysis_v3.txt 和 rejects 清单
python build_analysis_v3.py

# 5. 先看选片，不切割
python run_promo_molecular.py --dry-run --types hook_clash,suspense_hook

# 6. 生成6类分子宣发，并输出 qa_<type>.json
python run_promo_molecular.py --types hook_clash,identity_twist,emotional_resonance,quote_rhythm,cinematic_beauty,suspense_hook
```

常用口令：

```
说"做宣发视频" → 自动触发全流程：审片→裁剪→特效→字幕→导出
说"切出第15集1分20秒到2分10秒" → §1 片段切割
说"给这段降噪" → §11 音频精修
说"导出给Premiere" → §8 独立裁剪片段
```

## V2/V3 数据字段

- **V2（感知层）**：VL 输出趋势标签 `action_direction`(增强/持续/静止/减弱) + `emotion_trend`(上升/稳定/下降/爆发) + 传统描述字段(shot/event/chars/hint 等)。emo/action_level/visual_quality 由 V3 规则层从趋势组合推导，不依赖 VL 打分。
- **V3（决策层）**：两阶段处理——趋势组合→原始分数 → 全剧分位数熔断(top-5%→hook=5)。输出 `promo_value/hook_value/cut_role/best_cut/pre_roll/post_roll/suggested_duration` 等决策字段。
- **架构决策**：VL 只做定性趋势描述，不做绝对数字评分。避免 VL 评分膨胀（历史教训：单帧 emo 分散但描述差，多帧描述好但 93% 挤在 emo=3，趋势标签+V3规则推导彻底解决）。

## 运行建议

1. `python multi_frame_sample.py --eps 1,7,29 --workers 1`
2. `python build_analysis_v3.py`
3. `python run_promo_molecular.py --dry-run --types hook_clash,identity_twist,emotional_resonance,quote_rhythm,cinematic_beauty,suspense_hook`
4. `python run_promo_molecular.py --types hook_clash,identity_twist,emotional_resonance,quote_rhythm,cinematic_beauty,suspense_hook`

完整文档见 `SKILL.md`。
