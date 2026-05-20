# 短剧宣发 · 全流程剪辑工作台

> 剧集切割 · 创意混剪 · 批量包装 · 宣发预告片 · 审片选段 · 封面生成 · 音频精修 · 批量流水线 · Premiere/AE 导出

## 能力总览

| 模块 | 内容 |
|------|------|
| **基础剪辑** (§1-§5) | 片段切割、合并拼接、创意混剪（画中画/变速/倒放）、批量包装（片头尾/字幕/水印）、音画处理（提取/替换/横竖转换/压缩） |
| **宣发制作** (§6) | 帧采样→vision.js AI识图→xfade转场组装→BGM混音→字幕烧录→一键出片 |
| **交付导出** (§7-§8) | XML/EDL交换格式生成器、独立裁剪片段（Premiere/AE 100%兼容） |
| **剪辑方法论** (§9-§10) | 黄金3-15-30爆款公式、7种情绪钩子模板、ffmpeg特效命令（闪白/震屏/变速/zoom）、去重六件套、音效设计、分子级审片拆解法 |
| **专业进阶** (§11) | 封面生成（智能选帧+标题叠加）、6段式音频精修链（降噪→EQ→齿音消除→压缩→响度标准化）、5阶段批量流水线 |

## 工具脚本

| 脚本 | 功能 |
|------|------|
| `multi_frame_sample.py` | 场景检测 + 多帧采样 + 千问VL结构化识图，输出 V2 质量字段 |
| `build_analysis_v3.py` | 离线把 `analysis_v2.txt` 升级为剪辑决策型 `analysis_v3.txt` |
| `run_promo_molecular.py` | 6类分子宣发生成：冲突钩子/身份反转/情感共鸣/金句卡点/光影美学/悬念钩子 |
| `edit_utils.py` | V2/V3解析、片段切割、音频/事实QA、并行工具 |
| `genre_engine.py` | 类型识别、方向推荐、叙事功能标签、分集多样性 |
| `scripts/xfade_gen.js` | 自动生成多片段 xfade 转场组接的 ffmpeg 命令 |
| `scripts/edl_gen.js` | CMX3600 EDL 编辑决策表生成（Premiere 兼容） |
| `scripts/fcp_xml_gen.js` | FCP 7 XML 生成（含 ffprobe 源时长探测、Cross Dissolve 转场、URL 编码） |

## 环境要求

- **ffmpeg / ffprobe** — Windows winget 安装，路径自动探测
- **Node.js** — 脚本运行环境
- **vision.js**（可选）— AI 帧内容分析，需千问 VL API key

## 快速开始

```bash
# 1. 配置 ffmpeg / ffprobe / DashScope Key
cp config.example.json config.json

# 2. 多帧采样，生成 analysis_v2.txt
python multi_frame_sample.py --eps 1,7,29 --workers 1
python multi_frame_sample.py --resume --workers 2

# 3. 离线升级剪辑决策数据，生成 analysis_v3.txt 和 rejects 清单
python build_analysis_v3.py

# 4. 先看选片，不切割
python run_promo_molecular.py --dry-run --types hook_clash,suspense_hook

# 5. 生成6类分子宣发，并输出 qa_<type>.json
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

- V2：`usable/reject_reason/visual_quality/face_quality/action_level/event_subtype/timestamp`，用于过滤白屏、黑屏、片头、纯文字、模糊帧。
- V3：`promo_value/hook_value/emotion_value/action_value/visual_value/conflict_side/cut_role/best_cut/pre_roll/post_roll/suggested_duration`，用于决定镜头能不能用、适合放哪里、截多长。

完整文档见 `SKILL.md`（11 章，850+ 行）。
