---
name: videocut:短剧宣发
description: 剧集视频剪辑工作台。切割/合并/混剪/包装/格式转换/宣发预告片。触发词：剪辑、切割、合并、混剪、宣发、预告片、做视频
---

# 剧集剪辑工作台

> 片段切割 · 合并拼接 · 创意混剪 · 批量包装 · 宣发预告片

## 意图路由

| 用户说 | 跳转 |
|--------|------|
| 切/截/剪出某段，把第X集XX秒到XX秒... | → §1 片段切割 |
| 合并/拼接/接起来/拼在一起 | → §2 合并拼接 |
| 混剪/画中画/快放/慢放/倒放 | → §3 创意混剪 |
| 批量加片头片尾/批量字幕/批量水印 | → §4 批量包装 |
| 提取音频/转格式/裁剪画面/缩放/加水印 | → §5 音画处理 |
| 宣发/预告片/推广视频/做宣发/切片/二创/去重 | → §6 宣发预告片 + §9 剪辑方法论 |
| 导出Premiere/导出AE/EDL/母版/导出素材 | → §7 Premiere/AE导出 或 §8 独立裁剪片段 |
| 爆款公式/钩子/节奏/转场特效/BGM/音效 | → §9 剪辑方法论 |
| 审片/选段/爽点/虐点/怎么看剧/哪些片段好 | → §10 审片方法论 |
| 封面/缩略图/降噪/声音处理/齿音/响度 | → §11 封面+音频精修 |
| 批量/流水线/一键出片/全自动 | → §11.3 批量流水线 + §12.3 一键出片 |
| 调色/滤镜/LUT/暖调/冷调 | → §12.1 调色预设 |
| 卡拉OK字幕/逐词高亮/ASS字幕 | → §12.2 高级字幕 |

---

## 公共环境

```bash
# ffmpeg 路径（Windows winget 安装）
FFMPEG="C:/Users/Administrator/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-full_build/bin"

# 本剧原片：720×1280 竖屏（9:16），共 76 集
# BGM：clips/ambient.wav（155秒）
# 识图：node vision.js
```

---

## §1 片段切割

```
用户: 把第15集1分20秒到2分10秒切出来
用户: 从第3集截取30秒到45秒
用户: 把这段剪掉开头5秒
```

### 无损切割（推荐，秒级精度）

```bash
"$FFMPEG/ffmpeg.exe" -y -ss <开始秒> -t <时长秒> -i "第XX集.mp4" -c copy output.mp4
```

### 精确切割（帧级精度，需重编码）

```bash
"$FFMPEG/ffmpeg.exe" -y -ss <开始秒> -t <时长秒> -i "第XX集.mp4" \
  -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k output.mp4
```

### 批量切割（多段来自同一集）

```bash
# 从第8集切3段
"$FFMPEG/ffmpeg.exe" -y -ss 10 -t 8 -i "第08集.mp4" -c copy cut_01.mp4
"$FFMPEG/ffmpeg.exe" -y -ss 45 -t 12 -i "第08集.mp4" -c copy cut_02.mp4
"$FFMPEG/ffmpeg.exe" -y -ss 90 -t 6 -i "第08集.mp4" -c copy cut_03.mp4
```

### 去头去尾

```bash
# 去掉开头3秒和结尾5秒
"$FFMPEG/ffmpeg.exe" -y -ss 3 -to $(ffprobe -v error -show_entries format=duration -of csv=p=0 input.mp4 | awk '{print $1-5}') -i input.mp4 -c copy output.mp4
```

---

## §2 合并拼接

```
用户: 把这三段视频接起来
用户: 按顺序合并 clip1 clip2 clip3
```

### 无损拼接（同格式同参数）

```bash
# 创建文件列表
echo "file 'clip1.mp4'" > concat.txt
echo "file 'clip2.mp4'" >> concat.txt
echo "file 'clip3.mp4'" >> concat.txt

# 拼接
"$FFMPEG/ffmpeg.exe" -y -f concat -safe 0 -i concat.txt -c copy merged.mp4
```

### 带转场的拼接（xfade，需重编码）

用 `scripts/xfade_gen.js` 生成命令：

```bash
node "C:/Users/Administrator/.claude/skills/短剧宣发/scripts/xfade_gen.js" 6 9 12 8
# 输出完整 ffmpeg 命令，0.3s 淡入淡出转场
```

### 混合格式拼接（不同分辨率/编码 → 统一后拼接）

```bash
# 先统一所有片段到相同参数
for f in *.mp4; do
  "$FFMPEG/ffmpeg.exe" -y -i "$f" \
    -vf "scale=1080:1920:flags=lanczos,fps=30" \
    -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p \
    -c:a aac -b:a 192k -ar 44100 -ac 2 \
    "norm_$f"
done
# 然后 concat 或 xfade
```

---

## §3 创意混剪

```
用户: 把A和B做成画中画
用户: 这段快放2倍
用户: 倒放这段
```

### 画中画（小窗叠加）

```bash
# 小窗在右下角，占 1/4 宽度
"$FFMPEG/ffmpeg.exe" -y \
  -i main.mp4 -i pip.mp4 \
  -filter_complex "\
    [1:v]scale=iw/3:-1[pip];\
    [0:v][pip]overlay=W-w-20:H-h-20\
  " -c:a copy -c:v libx264 -preset fast -crf 21 output.mp4

# 小窗在左上角
overlay=20:20

# 小窗在右上角
overlay=W-w-20:20
```

### 左右分屏

```bash
"$FFMPEG/ffmpeg.exe" -y \
  -i left.mp4 -i right.mp4 \
  -filter_complex "\
    [0:v]crop=iw/2:ih:0:0,scale=540:1920[l];\
    [1:v]crop=iw/2:ih:ow:0,scale=540:1920[r];\
    [l][r]hstack\
  " -c:v libx264 -preset fast -crf 21 output.mp4
```

### 变速

```bash
# 2倍速（画面+音频）
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -filter_complex "[0:v]setpts=0.5*PTS[v];[0:a]atempo=2[a]" \
  -map "[v]" -map "[a]" -c:v libx264 -preset fast -crf 21 output.mp4

# 慢放 0.5 倍
setpts=2*PTS + atempo=0.5

# 快放 1.5 倍
setpts=0.666*PTS + atempo=1.5
```

### 倒放

```bash
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -vf "reverse" -af "areverse" \
  -c:v libx264 -preset fast -crf 21 output.mp4
```

### 定格帧（冻结最后一帧 N 秒）

```bash
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -filter_complex "tpad=stop_mode=clone:stop_duration=3" \
  -c:v libx264 -preset fast -crf 21 -c:a copy output.mp4
```

---

## §4 批量包装

```
用户: 给所有片段加统一的片头片尾
用户: 给这10集批量烧录字幕
用户: 批量导出带水印的版本
```

### 加片头片尾

```bash
# 准备片头 intro.mp4 和片尾 outro.mp4（与本片同分辨率）
echo "file 'intro.mp4'" > pack.txt
echo "file '正片.mp4'" >> pack.txt
echo "file 'outro.mp4'" >> pack.txt

"$FFMPEG/ffmpeg.exe" -y -f concat -safe 0 -i pack.txt -c copy output.mp4
```

### 批量烧录字幕

如果多个正片共用同一个 SRT 字幕模板（如片名水印）：

```bash
for ep in 01 02 03 04 05; do
  "$FFMPEG/ffmpeg.exe" -y -i "第${ep}集.mp4" \
    -vf "subtitles='template.srt':force_style=\
'FontSize=24,FontName=Microsoft YaHei,Bold=1,\
PrimaryColour=&H0000deff,OutlineColour=&H00000000,\
Outline=2.5,Alignment=2,MarginV=50'" \
    -c:a copy -c:v libx264 -preset fast -crf 21 \
    "第${ep}集_字幕.mp4"
done
```

### 批量加水印

```bash
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 -i logo.png \
  -filter_complex "overlay=W-w-20:20" \
  -c:a copy -c:v libx264 -preset fast -crf 21 output.mp4
```

---

## §5 音画处理 + 格式转换

```
用户: 提取这个视频的音频
用户: 把横屏转成竖屏
用户: 压缩视频大小
```

### 提取音频

```bash
# MP3
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 -vn -acodec libmp3lame -b:a 192k output.mp3

# AAC
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 -vn -acodec aac -b:a 192k output.aac

# WAV（无损）
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 -vn -acodec pcm_s16le output.wav
```

### 替换音频

```bash
"$FFMPEG/ffmpeg.exe" -y -i video.mp4 -i new_audio.mp3 \
  -c:v copy -c:a aac -b:a 192k -map 0:v -map 1:a -shortest output.mp4
```

### 横竖转换

```bash
# 横屏 16:9 → 竖屏 9:16（居中裁剪）
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -vf "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos" \
  -c:a copy -c:v libx264 -preset medium -crf 21 output.mp4

# 竖屏 9:16 → 横屏 16:9（居中裁剪 + 模糊背景可选）
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -vf "crop=iw*9/16:iw,scale=1920:1080:flags=lanczos" \
  -c:a copy -c:v libx264 -preset medium -crf 21 output.mp4
```

### 视频压缩

```bash
# 降低码率（画质优先）
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -c:v libx264 -preset medium -crf 28 -c:a aac -b:a 128k output.mp4

# 降低分辨率
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -vf "scale=720:1280:flags=lanczos" \
  -c:v libx264 -preset medium -crf 23 -c:a aac -b:a 128k output.mp4
```

### 静音段删除

```bash
# 删除超过1.5秒的静音
"$FFMPEG/ffmpeg.exe" -y -i input.mp4 \
  -af "silenceremove=stop_periods=-1:stop_duration=1.5:stop_threshold=-40dB" \
  -c:v copy -c:a aac -b:a 192k output.mp4
```

### 混合 BGM

```bash
"$FFMPEG/ffmpeg.exe" -y \
  -i video.mp4 -i bgm.wav \
  -filter_complex "\
    [1:a]atrim=0:60,volume=0.3,afade=t=out:st=57:d=3[bgm];\
    [0:a][bgm]amix=inputs=2:duration=first[amix];\
    [amix]afade=t=out:st=57:d=3[aout]\
  " -map 0:v -map "[aout]" \
  -c:v copy -c:a aac -b:a 192k output.mp4
```

---

## §6 宣发预告片

```
用户: 帮我做宣发视频
用户: 给《终宋》做抖音预告片
用户: 剪推广视频
```

### 核心流程

```
1. 确认需求（时长/平台/字幕/素材）
    ↓
2. 帧采样 + vision.js 识图定位高冲突片段
    ↓
3. 提取片段 (ffmpeg -ss -t)
    ↓
4. 竖屏升频 + xfade 转场组装
    ↓
5. 混入 BGM
    ↓
6. 字幕制作（可选，手动 SRT 推荐）
    ↓
7. 烧录字幕 → 导出
```

### 6.1 确认需求

| 问题 | 选项 |
|------|------|
| 平台 | 抖音/视频号（竖屏 9:16, 30-60s）/ B站（横屏 16:9, 2-5min） |
| 素材 | 从已有 clips / 从正片重新挑选 |
| 字幕 | 需要 / 不需要 |
| 时长 | 默认 40-50 秒 |

### 6.2 内容筛选

```bash
# 1. 优先从已知高冲突集数每隔 15s 提取帧
for ep in 38 45 64 66 67 73; do
  mkdir -p "promo_frames/ep${ep}"
  "$FFMPEG/ffmpeg.exe" -y -i "第${ep}集.mp4" \
    -vf "fps=1/15" "promo_frames/ep${ep}/frame_%03d.jpg"
done

# 2. vision.js 批量识图
for f in promo_frames/ep*/*.jpg; do
  echo "=== $f ==="
  node vision.js "$f" "场景类型、人数、情绪强度(1-5)。15字以内"
done

# 3. 筛选：战斗/对峙 ≥ 情绪4分 → hook/build/rise/climax/end 各1个
```

### 6.3 提取 + 修剪 + 组装

用 `scripts/xfade_gen.js` 一键生成命令：

```bash
node "C:/Users/Administrator/.claude/skills/短剧宣发/scripts/xfade_gen.js" 6 9 12 9 8
```

### 6.4 字幕

手动编写 SRT（短预告 10-20 句戏剧性短语）：

```bash
# 烧录字幕（抖音标准样式）
"$FFMPEG/ffmpeg.exe" -y -i trailer_bgm.mp4 \
  -vf "subtitles='subtitle.srt':force_style=\
'FontSize=26,FontName=Microsoft YaHei,Bold=1,\
PrimaryColour=&H0000deff,OutlineColour=&H00000000,\
Outline=2.5,Alignment=2,MarginV=60'" \
  -c:a copy -c:v libx264 -preset medium -crf 21 -pix_fmt yuv420p -movflags +faststart \
  终宋_短视频宣发.mp4
```

---

## §7 Premiere Pro / After Effects 导出

```
用户: 导出给Premiere
用户: 转成PR能打开的格式
用户: 导出AE源文件
用户: 生成母版给后期
```

### ⚠️ 重大教训：XML/EDL 在 Premiere 2025 中不可靠

经多轮调试（URL编码、duration对齐、ASCII卷名、结构对标Premiere导出格式），**FCP 7 XML 和 CMX3600 EDL 在 Premiere Pro 2025 + 中文文件名场景下始终媒体离线**，已放弃此路线。

**可靠方案：直接渲染独立裁剪片段 → §8，拖入 Premiere 即可用。**

脚本保留供参考（或许在其他 Premiere 版本/纯英文路径下可用）：
- `scripts/fcp_xml_gen.js` — FCP 7 XML 生成器（含 ffprobe 探测源时长、URL 编码、Cross Dissolve）
- `scripts/edl_gen.js` — CMX3600 EDL 生成器（ASCII 卷名）

### 7.1 高质量母版转码

将成片转为 Premiere/AE 编辑友好的中间格式。

**DNxHR HQ（推荐，Windows）：**

```bash
"$FFMPEG/ffmpeg.exe" -y -i 终宋_短视频宣发.mp4 \
  -c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p \
  -c:a pcm_s16le -ar 48000 \
  终宋_母版_DNxHR.mov
```

**ProRes 422 HQ（Mac 首选）：**

```bash
"$FFMPEG/ffmpeg.exe" -y -i 终宋_短视频宣发.mp4 \
  -c:v prores_ks -profile:v 3 -pix_fmt yuv422p10le \
  -c:a pcm_s16le -ar 48000 \
  终宋_母版_ProRes.mov
```

**ProRes 4444（含 Alpha 通道，适合 AE 合成）：**

```bash
"$FFMPEG/ffmpeg.exe" -y -i 终宋_短视频宣发.mp4 \
  -c:v prores_ks -profile:v 4 -pix_fmt yuva444p10le \
  -c:a pcm_s16le -ar 48000 \
  终宋_母版_ProRes4444.mov
```

**母版格式对比：**

| 格式 | 比特率 | 画质 | 文件大小(42s) | 适用 |
|------|--------|------|--------------|------|
| DNxHR HQ | ~220 Mbps | 优秀 | ~1.2 GB | Windows/Premiere |
| ProRes 422 HQ | ~280 Mbps | 优秀 | ~1.5 GB | Mac/Premiere/AE |
| ProRes 4444 | ~350 Mbps | 无损 | ~1.8 GB | AE 特效合成 |
| H.264 CRF18 | ~15 Mbps | 很好 | ~80 MB | 预览/交付 |

### 7.2 分轨音频导出

将成片的对白和 BGM 分离，方便在 Premiere/AE 中独立调整。

```bash
# 对白轨（从组装后但未混BGM的视频提取）
"$FFMPEG/ffmpeg.exe" -y -i trailer_assembled.mp4 \
  -vn -acodec pcm_s16le -ar 48000 \
  终宋_对白轨.wav

# BGM轨（纯背景音乐）
"$FFMPEG/ffmpeg.exe" -y -i clips/ambient.wav \
  -acodec pcm_s16le -ar 48000 \
  终宋_BGM轨.wav
```

### 7.3 一键导出套装

成品导出时同时生成所有格式：

```bash
OUTPUT_DIR="exports/终宋_宣发"
mkdir -p "$OUTPUT_DIR"

# 1. 交付版 H.264
cp 终宋_短视频宣发.mp4 "$OUTPUT_DIR/"

# 2. 独立裁剪片段（推荐给 Premiere/AE 用，§8）
mkdir -p "$OUTPUT_DIR/trimmed_clips"
# 每个片段: ffmpeg -ss <start> -t <dur> -i <source> ... -crf 18 clip_NN.mp4

# 3. 母版 DNxHR
ffmpeg -y -i 终宋_短视频宣发.mp4 \
  -c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p \
  -c:a pcm_s16le -ar 48000 \
  "$OUTPUT_DIR/终宋_母版_DNxHR.mov"

# 4. 分轨音频
ffmpeg -y -i trailer_assembled.mp4 -vn -acodec pcm_s16le "$OUTPUT_DIR/对白轨.wav"
ffmpeg -y -i clips/ambient.wav -acodec pcm_s16le "$OUTPUT_DIR/BGM轨.wav"

echo "✅ 导出完成: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR"
```

---

## §8 独立裁剪片段导出（Premiere / AE 可靠方案）

```
用户: 导出素材给Premiere
用户: 把片段裁出来给后期
```

**直接渲染每个片段为独立 mp4 文件**，拖入任何编辑软件即可使用，100% 兼容。

### 用法

对每个片段执行：

```bash
"$FFMPEG/ffmpeg.exe" -y -ss <开始秒> -t <时长秒> -i "源文件.mp4" \
  -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k \
  -vf "scale=1080:1920:flags=lanczos,fps=30" \
  "clip_NN_标签_源文件.mp4"
```

### 示例（终宋宣发 5 段）

```bash
SRC="E:/BaiduNetdiskDownload/05.终宋（76集）陈外＆王涵"
OUT="$SRC/clips_promo/trimmed_clips"
mkdir -p "$OUT"
FF="<ffmpeg路径>/ffmpeg.exe"

"$FF" -y -ss 58 -t 6  -i "$SRC/第73集.mp4" -vf "scale=1080:1920,fps=30" -crf 18 "$OUT/clip01_对峙_第73集.mp4"
"$FF" -y -ss 22 -t 9  -i "$SRC/第67集.mp4" -vf "scale=1080:1920,fps=30" -crf 18 "$OUT/clip02_桥头七人对峙_第67集.mp4"
"$FF" -y -ss 5  -t 12 -i "$SRC/第66集.mp4" -vf "scale=1080:1920,fps=30" -crf 18 "$OUT/clip03_行军打斗_第66集.mp4"
"$FF" -y -ss 28 -t 9  -i "$SRC/第73集.mp4" -vf "scale=1080:1920,fps=30" -crf 18 "$OUT/clip04_武士激战_第73集.mp4"
"$FF" -y -ss 80 -t 8  -i "$SRC/第73集.mp4" -vf "scale=1080:1920,fps=30" -crf 18 "$OUT/clip05_持剑独立_第73集.mp4"
```

### 在 Premiere 中使用

1. 文件 → 导入 → 全选 clip*.mp4
2. 按序号拖到时间轴
3. 片段间加 Cross Dissolve 转场（前后重叠 xfade_dur 秒）
4. 导入 BGM 轨 → 混音 → 调色 → 导出

### 对比

| | XML/EDL 交换 | 独立裁剪片段 |
|---|---|---|
| Premiere 2025 兼容 | ❌ 媒体离线 | ✅ 直接可用 |
| 转场保留 | 理论支持 | 手动添加 |
| 文件数量 | 1 个元数据文件 | N 个 mp4 文件 |

---

## ffmpeg 参数速查

| 参数 | 值 | 用途 |
|------|------|------|
| `-ss 10 -t 5` | 从10秒开始取5秒 | 片段切割 |
| `-c copy` | 不重编码 | 无损切割/拼接 |
| `-crf 18` | 近乎无损 | 精确切割重编码 |
| `-crf 21` | 高质量 | 组装/烧录字幕 |
| `-crf 28` | 压缩 | 减小体积 |
| `-preset medium` | 平衡 | 最终导出 |
| `-preset fast` | 快 | 中间产物 |
| `-vf scale=1080:1920` | 升频 | 竖屏1080p |
| `-vf fps=30` | 统一帧率 | 组装前 |
| `-movflags +faststart` | 网页优化 | 最终导出 |

## 经验教训

### 2026-05-17《终宋》

- **ffmpeg Windows 路径**：winget 安装后在 `%LOCALAPPDATA%/Microsoft/WinGet/Packages/Gyan.FFmpeg_*/bin/`
- **原片是竖屏**：先 `ffprobe` 检查分辨率，不要盲目裁剪
- **xfade 组装前所有 clip 必须统一 fps**：`fps=30` 放到 filter 链最前面
- **acrossfade 要求音频格式一致**：用 `aformat=sample_rates=44100:channel_layouts=stereo` 统一
- **vision.js 帧分析可行**：每15秒一帧快速定位高冲突集数
- **宣发字幕短文案优先**：戏剧性短语 > 逐句对白
- **clip 总时长 40-50 秒**最适合抖音，转场后约 42 秒
- **Premiere/AE 导入 XML/EDL 不可靠**：经多轮调试（URL编码、duration对齐、ASCII卷名、结构对标Premiere导出格式），FCP XML 和 CMX3600 EDL 在 Premiere Pro 2025 + 中文文件名场景下始终媒体离线。**结论：放弃 XML/EDL 交换格式，改用直接渲染独立裁剪片段**。
- **独立裁剪片段是可靠方案**：用 ffmpeg 从源文件精确裁剪每个片段（`-ss -t` + 重编码），输出独立 mp4，拖入 Premiere 即可用。详见 §8。
- **CMX3600 EDL 与 UTF-8 不兼容**：中文卷名破坏固定字节列对齐，即使改用 ASCII 卷名、`* FROM CLIP NAME` 保留原名，仍无法可靠导入。
- **FCP XML 的 clipitem/duration 含义**：对标 Premiere 导出格式验证，`clipitem/duration` = 片段在时间轴上的时长（end-start），`file/duration` = 源文件完整时长（需 ffprobe 探测）。但即使结构完全对标仍导入失败。

---

## §9 短剧剪辑 & 宣发切片方法论

```
用户: 怎么做爆款切片
用户: 二创手法有哪些
用户: 怎么去重
用户: 怎么剪出节奏感
```

### 9.1 爆款结构：「黄金3-15-30」公式

```
[0-3秒]   极端冲突/悬念钩子 → 打破心理预期
[3-15秒]  高能混剪 → 3-5 个最精彩画面快拼
[15-30秒] 情绪落差 → 甜→虐→爽的极端反差
[结尾]    悬崖效应留白 → 关键处戛然而止，引导"看全集"
```

**关键数据**：带"强钩子"的切片初始播放量比无剪辑作品高 **2.8 倍**，跳转率 15%-20%。

### 9.2 7 种情绪钩子模板

| 类型 | 句式 | 适用 |
|------|------|------|
| 疑问挑战 | "你敢信？……" | 反常识 |
| 反常识宣言 | "我被骂了三年，但今天必须说出真相" | 情感 |
| 场景痛点 | "凌晨三点的急诊室，护士突然递给我一张纸条……" | 剧情 |
| 数字冲击 | "从月薪3千到5万，我只做了这1件事" | 职场 |
| 身份代入 | "所有女生！""二胎妈妈的凌晨崩溃" | 美妆/亲子 |
| 悬念前置 | "最后一步做错，前面全白费" | 教程 |
| 视觉暴力 | 慢镜头+特效+强音效 | 动作 |

**前3秒核心法则**：≤12 字强冲击台词，配合画面指令+音效（玻璃碎裂/心跳骤停），3秒内只传递 1 个信息。

### 9.3 ffmpeg 剪辑特效命令

#### 闪白转场（冲突爆发点）
```bash
# 0.1s 闪白 + "咔"音效
"$FFMPEG/ffmpeg.exe" -i input.mp4 -filter_complex \
  "color=white:s=1080x1920:d=0.1,format=rgba[flash]; \
   [0:v][flash]overlay=enable='between(t,2,2.1)'" output.mp4
```

#### 变速呼吸感（0.95x→1.0x→1.05x）
```bash
# 非对话区微加速，高潮恢复原速
"$FFMPEG/ffmpeg.exe" -i input.mp4 \
  -vf "setpts=0.95*PTS,fps=30" -af "atempo=1.05" output.mp4
```

#### 推进放大（zoom in 强调）
```bash
# 从 1.0x 缓慢推到 1.15x
"$FFMPEG/ffmpeg.exe" -i input.mp4 -vf \
  "scale=4320:7680,zoompan=z='min(max(zoom,pzoom)+0.0005,1.15)':d=1:\
   x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30" output.mp4
```

#### 震屏效果（打斗/冲突瞬间）
```bash
# ±15px 震动，持续 0.3s，30Hz
"$FFMPEG/ffmpeg.exe" -i input.mp4 -vf \
  "crop=iw-30:ih-30:15+15*sin(2*PI*30*t)*between(t,1,1.3):\
   15+15*cos(2*PI*30*t)*between(t,1,1.3),scale=1080:1920" output.mp4
```

#### xfade 转场组接
```bash
# 多片段 + 0.3s 淡入淡出（xfade + acrossfade）
"$FFMPEG/ffmpeg.exe" -i c1.mp4 -i c2.mp4 -i c3.mp4 -filter_complex \
  "[0:v]fps=30,setpts=PTS-STARTPTS[v0]; \
   [1:v]fps=30,setpts=PTS-STARTPTS[v1]; \
   [2:v]fps=30,setpts=PTS-STARTPTS[v2]; \
   [v0][v1]xfade=transition=fade:duration=0.3:offset=5.7[vt1]; \
   [vt1][v2]xfade=transition=fade:duration=0.3:offset=14.4[vt2]; \
   [0:a]aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0]; \
   [1:a]aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1]; \
   [2:a]aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS[a2]; \
   [a0][a1]acrossfade=d=0.3[at1]; \
   [at1][a2]acrossfade=d=0.3[aout]" \
  -map "[vt2]" -map "[aout]" output.mp4
```

### 9.4 二创去重六件套

| 手法 | ffmpeg/操作 |
|------|------------|
| ① 掐头去尾+重排 | `-ss 2` 去开头，片段打乱顺序组接 |
| ② 镜像翻转 | `-vf "hflip"` |
| ③ 缩放重构 | `-vf "scale=1080:1920, crop=...` 放大 110% 重新构图 |
| ④ 分段变速 | 非对话 0.95x，高潮 1.0x，结尾 1.05x |
| ⑤ BGM 全量置换 | `-filter_complex "[0:a]volume=0[a];[1:a]...[a]amix"` |
| ⑥ 抽帧 | 用 `fps=29` 替代 `fps=30`，自然丢帧 |

**去重原则**：单一手段效果有限，**4 项基础改造 + 3 项深度伪装**组合使用。

### 9.5 音效设计

| 场景 | 音效 | ffmpeg 参数 |
|------|------|------------|
| 转场 | 短促 "咔""咻" | 叠加 0.1s 白噪 |
| 悬念 | 心跳+滴答 | 低频增强 `-af "equalizer=f=50:t=q:w=1:g=5"` |
| 爽点 | 金属撞击/爆炸 | BGM 卡点燃曲 |
| 沉默 | 高潮后突然静音 | `-af "volume=enable='between(t,5,6)':volume=0"` |

### 9.6 节奏铁律

> "平淡是原罪。你不是在剪视频——你是在用户脑子里搞一场微型风暴。"

- 单镜头 ≤ 3 秒，高潮冲突压缩至 0.5-1 秒
- 每 15 秒设一个信息爆点（反转/音效/视觉冲击）
- 每 12 秒插入差异化音效
- 高潮后留 1-2 秒「呼吸停顿」再进下一段
- 删掉所有「脂肪镜头」：空洞走路、无意义空镜、拖沓对白

### 9.7 全流程速查

```
1. 识图定位高冲突片段（vision.js §6.2）
      ↓
2. 独立裁剪导出（§8）或用 xfade 直接组装（§9.3）
      ↓
3. 变速 + 闪白 + 震屏 + zoom 特效叠加（§9.3）
      ↓
4. 混入卡点 BGM + 音效埋点（§9.5）
      ↓
5. 烧录冲击字幕（§6.4）→ 导出 1080×1920 H.264
      ↓
6. 封面标题：「冲突词+数字+身份锚点」结构
      ↓
7. 发布标签：热点标签 + 垂直标签 + 品牌标签 三层嵌套
```

Sources:
- [短剧100问：从剪辑引流到二创狂欢](https://www.sohu.com/a/941212417_351788)
- [揭秘爆款短剧推广剪辑手法](https://www.douyin766.com/182132.html)
- [短剧投流素材创意手册](https://www.gansuci.cn/2026/0511/474393.shtml)
- [视频二创二剪去重 2025最新规则](http://mp.weixin.qq.com/s?__biz=MzA3NDI0MjgyMQ==&mid=2247503387&idx=2&sn=b02c32ede93fc4a6ff0f44948ee84969)
- [抖音爆款文案解剖](http://www.360doc.com/content/25/0607/05/3936723_1155006683.shtml)

---

## §10 审片方法论：如何看剧选出爆款片段

```
用户: 怎么审片
用户: 怎么选段
用户: 哪些片段容易爆
用户: 爽点怎么看
```

> 审片不是看剧——是用手术刀把剧拆成可传播的情绪单元。

### 10.1 五层递进审片框架

拿到一集剧，按这五层从粗到细扫描：

| 层级 | 审片问题 | 判断标准 |
|------|---------|---------|
| **情节** | 这段戏推动了主线吗？ | 有起因→发展→高潮→结局 |
| **事件** | 这个场景承载有效信息吗？ | 无冗余、无"脂肪镜头" |
| **冲突** | 有"困兽结构"吗？ | 角色被困在物理/心理空间，观众代入"如何挣脱" |
| **钩子** | 有让观众"忍不住想看下去"的细节吗？ | 反转前0.5秒的微表情、未说完的半句话 |
| **情绪** | 能触发哪种情绪？ | 爽/虐/甜/惊——必须纯粹，不能混杂 |

### 10.2 分子级拆解法（6 维度，1 部剧出 20+ 条素材）

```
传统按集剪 → 20-30 条，重复率 40%+
分子级拆解 → 20+ 条，完全不重样
```

| 分子类型 | 拆法 | 素材示例 |
|----------|------|---------|
| **情感分子** | 愤怒/感动/搞笑/心疼/爽感/悬念 → 每种情绪独立成片 | 打脸爽感版、心疼逆袭版 |
| **人物分子** | 同一场戏从不同角色视角切入 | 男主守护视角 vs 女主复仇视角 |
| **冲突分子** | 每场冲突独立提取为爆点 | 身份揭穿、豪门打脸、逆袭翻盘 |
| **节奏分子** | 同一段剪 3s卡点 / 15s情绪 / 30s故事 | 适配不同平台 |
| **悬念分子** | 反转前铺垫单独剪出 | 吊胃口 > 甩答案 |
| **金句分子** | 经典台词 + 不同 BGM = 新素材 | 名场面定格 + 卡点快剪 |

### 10.3 情绪价值判断：爽点 vs 虐点

#### 爽点识别

| 标准 | 判断方法 |
|------|---------|
| **触发明确情绪峰值** | 观众能直观感受"解气/甜蜜/逆袭" |
| **贴合预期 + 超出 10%** | 猜到要爽，结尾加个小反转 |
| **即时爽，不拖沓** | 30 秒铺垫 → 10-20 秒爆发 |

**爽点必备**：冲突前置（开篇 10 秒抛出核心冲突）+ 强弱对比清晰（先抑后扬）+ 落地彻底（不"嘴炮半天没行动"）

#### 虐点识别

| 类型 | 选段技巧 |
|------|---------|
| 被背叛/抛弃 | 选"崩溃前的情绪积累"，不选崩溃本身 |
| 被误解/羞辱 | 选"默默承受但眼神坚定"——这是逆袭的前奏 |
| 生离死别 | 选"将得未得"的临界点 |
| 身份落差 | 选"咬牙忍耐"的微表情特写 |

> ⚠️ **虐点铁律**：虐是为了让后面的爽更炸。纯虐无解 → 不剪。

### 10.4 付费卡点定位（最重要）

一部 80 集短剧通常设 3 个付费卡点，这些位置 = **最佳素材切入位置**：

| 卡点 | 位置 | 特征 | 优先级 |
|------|------|------|--------|
| **卡一** | 第 8-16 集 | 第一个重大转折：身份揭露前/被逼绝境/复仇临界点 | ⭐⭐⭐ 最优先 |
| **卡二** | 第 25-30 集 | 新危机/更大反转：强敌出现、复仇暴露 | ⭐⭐ |
| **卡三** | 第 50 集左右 | 终极冲突爆发、核心谜底揭晓 | ⭐ |

> 所有爆款卡点遵循 **"铺垫→高潮→截断"**——在最爽的地方卡住，这就是你该剪的位置。

### 10.5 快速审片清单

```
☐ 3 秒内有没有冲突/悬念/金句？（没有 → 跳过）
☐ 这段戏的情绪纯粹吗？（又爽又虐 → 拆成两条）
☐ 冲突是具象可视的吗？（抽象嘴炮 → 跳过）
☐ 人物有"困兽结构"吗？（没有代入感 → 跳过）
☐ 画面有没有可做封面的"定格瞬间"？（眼神/动作/反转表情）
☐ 最爽的地方被截断了吗？（截断点 = 你的切片结尾）
☐ 这一段和已发布素材重复率低于 30% 吗？
```

### 10.6 与 vision.js 协同：AI 辅助审片

已有 `vision.js` 可批量帧分析，审片时的 prompt 策略：

```bash
# 快速扫描全剧高冲突段（每 15s 一帧）
for ep in 38 45 64 66 67 73; do
  "$FFMPEG/ffmpeg.exe" -y -i "第${ep}集.mp4" \
    -vf "fps=1/15" "frames/ep${ep}/frame_%03d.jpg"
done

# 审片 prompt（按 §10 框架）
node vision.js "frame_042.jpg" \
  "短剧审片：1)冲突类型(打脸/身份反转/情感爆发/悬念) 
   2)情绪强度1-5 3)是否有困兽结构 4)是否适合做切片开头 
   5)画面中人物数量与站位。20字以内。"
```

### 10.7 选段 → 剪辑 全链路

```
审片(§10) → 定位情绪峰值时间戳
    ↓
帧采样识图(§6.2 + §10.6) → 批量验证 + 筛选
    ↓
独立裁剪(§8) → 精确提取片段
    ↓
特效+变速+音效(§9.3-9.5) → 二创加工
    ↓
字幕+BGM+封面(§6.4 + §9.7) → 导出发布
```

Sources:
- [一部短剧剪出20条不重复素材：内容拆解的分子级方法](https://vv.lmtw.com/mzw/content/detail/id/254165)
- [微短剧爆款营销思维：痛点爽点如何收割流量](https://vv.lmtw.com/mzw/content/detail/id/252763)
- [情节、事件、冲突、钩子、情绪五者联系](http://www.360doc.com/content/25/1101/06/69441939_1164116267.shtml)
- [7天拆完10部爆剧：短剧编剧靠拉片起飞](http://www.360doc.com/content/25/0910/12/5817836_1161065282.shtml)
- [短剧投流金字塔模型](https://blog.csdn.net/MUMUFD/article/details/160928158)

---

## §11 封面生成 & 音频精修 & 批量流水线

```
用户: 生成封面
用户: 音频降噪/声音处理
用户: 批量处理/一键出片
```

### 11.1 封面生成

#### 从视频抽封面帧
```bash
# 智能选帧（thumbnail 滤镜自动挑最具代表性的一帧）
"$FFMPEG/ffmpeg.exe" -ss 3 -i input.mp4 -frames:v 1 -an \
  -vf "thumbnail,scale=1080:1920:force_original_aspect_ratio=decrease,\
       pad=1080:1920:-1:-1:black,setsar=1" cover.jpg

# 场景变化检测（挑高冲突帧，scene 值越小越敏感）
"$FFMPEG/ffmpeg.exe" -i input.mp4 \
  -vf "select='gt(scene,0.3)',scale=1080:1920" \
  -vsync vfr frames/cover_%03d.jpg
```

#### 封面叠加冲突文案
```bash
"$FFMPEG/ffmpeg.exe" -i cover.jpg \
  -vf "drawtext=fontfile=/path/to/bold.ttf:\
       text='身份揭穿！她才是真正的豪门千金':\
       fontsize=72:fontcolor=white:\
       x=(w-text_w)/2:y=h-text_h-120:\
       box=1:boxcolor=black@0.6:boxborderw=10:\
       shadowcolor=black:shadowx=3:shadowy=3" \
  cover_titled.jpg
```

**封面文案公式**：`冲突词 + 数字 + 身份锚点`
> 例：`"被赶出家门第3天，前夫跪着求我回去"` / `"签完离婚协议，我亮出了隐藏身份"`

### 11.2 音频精修链

一条命令完成 **降噪 → 去嗡 → 均衡 → 齿音消除 → 压缩 → 响度标准化**：

```bash
"$FFMPEG/ffmpeg.exe" -i input.mp4 \
  -af "highpass=f=70, \
       equalizer=f=200:t=q:w=1:g=-2, \
       equalizer=f=3000:t=q:w=1.5:g=3, \
       deesser=i=0.5:f=6500:s=0:m=0, \
       acompressor=threshold=-20dB:ratio=2.5:attack=5:release=50:makeup=3, \
       loudnorm=I=-16:TP=-1.5:LRA=11:linear=true" \
  -c:v copy audio_cleaned.mp4
```

| 步骤 | 滤镜 | 作用 |
|------|------|------|
| 1 | `highpass=f=70` | 切除 70Hz 以下闷响/风噪 |
| 2 | `equalizer=200Hz -2dB` | 减少浑浊感 |
| 3 | `equalizer=3kHz +3dB` | 提升人声清晰度（短剧对白关键） |
| 4 | `deesser 6.5kHz` | 消除刺耳齿音（"是""吃""知"） |
| 5 | `acompressor 2.5:1` | 温和压缩动态范围 |
| 6 | `loudnorm -16 LUFS` | 抖音标准响度，避免被平台压缩 |

**两段式 loudnorm（更精确）**：
```bash
# 第一遍：测量
"$FFMPEG/ffmpeg.exe" -i input.mp4 -af "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json" -f null -

# 第二遍：用测量值修正
"$FFMPEG/ffmpeg.exe" -i input.mp4 \
  -af "loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=-20.5:measured_TP=-3.2:\
       measured_LRA=7.5:measured_thresh=-30.5:offset=1.5:linear=true" \
  -c:v copy output.mp4
```

### 11.3 批量流水线

#### 单剧全自动出片脚本框架
```bash
#!/bin/bash
# 一部剧 → N 条切片 → 全流程自动出片

SRC="E:/剧集目录"
OUT="$SRC/clips_output"
FF="<ffmpeg路径>/ffmpeg.exe"
mkdir -p "$OUT/01_frames" "$OUT/02_clips" "$OUT/03_edited" "$OUT/04_covers" "$OUT/05_final"

# === 阶段1: 帧采样审片 ===
echo "[1/5] 帧采样..."
for ep in 38 45 64 66 67 73; do
  "$FF" -y -i "$SRC/第${ep}集.mp4" \
    -vf "fps=1/15" "$OUT/01_frames/ep${ep}_%03d.jpg"
done
# → 用 vision.js 批量识图，标记高冲突帧及时间戳

# === 阶段2: 批量精确裁剪 ===
echo "[2/5] 精确裁剪..."
# 片段配置来自审片结果
declare -A clips=(
  ["clip01"]="第73集.mp4|58|6|对峙"
  ["clip02"]="第67集.mp4|22|9|桥头七人对峙"
  ["clip03"]="第66集.mp4|5|12|行军打斗"
)
for key in "${!clips[@]}"; do
  IFS='|' read -r src start dur label <<< "${clips[$key]}"
  "$FF" -y -ss "$start" -t "$dur" -i "$SRC/$src" \
    -vf "scale=1080:1920,fps=30" -crf 18 -preset fast \
    "$OUT/02_clips/${key}_${label}.mp4"
done

# === 阶段3: 批量特效+音频 ===
echo "[3/5] 特效+音频..."
for clip in "$OUT/02_clips"/*.mp4; do
  name=$(basename "$clip" .mp4)
  # 微加速 1.03x + 音频精修
  "$FF" -y -i "$clip" \
    -vf "setpts=0.97*PTS,fps=30" \
    -af "atempo=1.03,highpass=f=70,equalizer=f=3000:t=q:w=1.5:g=3,\
         deesser=i=0.5:f=6500:s=0:m=0,\
         acompressor=threshold=-20dB:ratio=2.5:attack=5:release=50:makeup=3,\
         loudnorm=I=-16:TP=-1.5:LRA=11" \
    "$OUT/03_edited/${name}_edit.mp4"
done

# === 阶段4: 批量封面 ===
echo "[4/5] 生成封面..."
for clip in "$OUT/03_edited"/*.mp4; do
  name=$(basename "$clip" _edit.mp4)
  # 抽第2秒帧 + 叠加标题
  "$FF" -y -ss 2 -i "$clip" -frames:v 1 -an \
    -vf "thumbnail,scale=1080:1920,drawtext=fontsize=64:fontcolor=white:\
         text='${name}':x=(w-text_w)/2:y=h-text_h-100:\
         box=1:boxcolor=black@0.6" \
    "$OUT/04_covers/${name}_cover.jpg"
done

# === 阶段5: 组装+BGM ===
echo "[5/5] 组装成片..."
# 用 xfade_gen.js 或直接 xfade
cd "$OUT/03_edited"
# 创建 concat 文件或 xfade 命令...
# → 叠加 BGM, 烧录字幕, 导出最终版

echo "✅ 全流程完成: $OUT/05_final"
```

#### 多剧种多平台批量导出
```bash
# 同一条素材输出三平台版本
for clip in "$OUT/03_edited"/*.mp4; do
  name=$(basename "$clip" .mp4)
  # TikTok 版：1080×1920, ≤60s
  "$FF" -y -i "$clip" -t 60 -vf "scale=1080:1920" -crf 21 \
    "$OUT/05_final/${name}_tiktok.mp4"
  # 视频号版：720×1280, ≤30s
  "$FF" -y -i "$clip" -t 30 -vf "scale=720:1280" -crf 21 \
    "$OUT/05_final/${name}_wechat.mp4"
  # B站版：1920×1080 (居中裁剪)
  "$FF" -y -i "$clip" -t 180 \
    -vf "crop=ih*9/16:ih,scale=1920:1080" -crf 20 \
    "$OUT/05_final/${name}_bilibili.mp4"
done
```

### 11.4 全技能总览

```
§10 审片（情绪峰值定位）
    ↓
§11.3 批量流水线（帧采样 → 批量裁剪 → 特效 → 封面 → 组装）
    ↓                    ↓              ↓
§6.2 vision.js      §11.1 封面     §11.2 音频精修
    ↓                    ↓              ↓
§8 独立裁剪          §9.3 特效     §6.4 字幕+BGM
    ↓
§11.3 多平台导出 → 发布
```

Sources:
- [FFmpeg thumbnail generation](https://www.tech-couch.com/post/extracting-video-covers-thumbnails-and-previews-with-ffmpeg)
- [FFmpeg voice post-production one-liner](https://lists.ffmpeg.org/pipermail/ffmpeg-user/2021-January/051593.html)
- [FFmpeg audio cleanup chain](https://superuser.com/posts/1393535/revisions)
- [ffmpeg-normalize loudnorm linear vs dynamic](https://github.com/slhck/ffmpeg-normalize/issues/274)
- [AI 短剧自动生成流水线](https://developer.volcengine.com/articles/7629571084442861606)
- [viral-shorts-engine](https://github.com/abc-kkk/viral-shorts-engine)
- [7天拆完10部爆剧：短剧编剧靠拉片起飞](http://www.360doc.com/content/25/0910/12/5817836_1161065282.shtml)
- [短剧投流金字塔模型](https://blog.csdn.net/MUMUFD/article/details/160928158)
---

## §12 一键出片 + 调色 + 高级字幕

```
用户: 一键出片
用户: 全自动生成
用户: 调色
用户: 卡拉OK字幕
```

### 12.1 调色预设

#### 内置滤镜调色（无需 LUT 文件）

```bash
# 甜宠暖调
"$FFMPEG/ffmpeg.exe" -i input.mp4 -vf \
  "eq=contrast=1.1:brightness=0.03:saturation=1.2,\
   colorbalance=rs=0.08:gs=-0.03:bs=-0.10" warm_romance.mp4

# 悬疑冷调
"$FFMPEG/ffmpeg.exe" -i input.mp4 -vf \
  "eq=contrast=1.15:brightness=-0.05:saturation=1.1,\
   colorbalance=rs=-0.10:gs=0:bs=0.12" cold_suspense.mp4

# 爽剧高饱和
"$FFMPEG/ffmpeg.exe" -i input.mp4 -vf \
  "eq=contrast=1.25:brightness=0.02:saturation=1.4:gamma=1.1,\
   curves=preset=strong_contrast" high_action.mp4
```

#### LUT 文件调色

```bash
# 下载 .cube LUT：https://luts.iwltbap.com/
"$FFMPEG/ffmpeg.exe" -i input.mp4 -vf "lut3d=look.cube" output.mp4

# 50% 调色强度叠加
"$FFMPEG/ffmpeg.exe" -i input.mp4 -filter_complex \
  "[0:v]split[v1][v2];[v1]lut3d=look.cube[lut];\
   [v2][lut]blend=all_mode=overlay:all_opacity=0.5" output.mp4
```

| 风格 | 滤镜链 | 适用剧种 |
|------|--------|---------|
| 暖调甜宠 | `eq=s=1.2 + colorbalance=rs=0.08:bs=-0.10` | 甜宠、古装言情 |
| 冷调悬疑 | `eq=contrast=1.15:br=-0.05 + colorbalance=rs=-0.10:bs=0.12` | 悬疑、虐恋 |
| 高饱和爽剧 | `eq=contrast=1.25:s=1.4 + curves=strong_contrast` | 逆袭、战神 |

### 12.2 高级字幕：ASS 卡拉OK逐词动画

#### 制作流程
```
1. Whisper 生成词语级时间戳字幕
2. Aegisub Karaoke Templater 添加 \k 标签
3. 导出 .ass → ffmpeg 烧录
```

#### 逐词高亮 ASS 模板

```ass
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Style: Default,Microsoft YaHei,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,80,1
Style: Active,Microsoft YaHei,56,&H0000deff,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,80,1

[Events]
# 每行重复全句，只高亮当前词（\rActive 切换样式）
Dialogue: 0,0:00:00.00,0:00:06.00,Default,,0,0,0,,{\rActive}你{\rDefault}以为你赢了？
Dialogue: 0,0:00:01.50,0:00:06.00,Default,,0,0,0,,你{\rActive}以为{\rDefault}你赢了？
Dialogue: 0,0:00:02.30,0:00:06.00,Default,,0,0,0,,你以为你{\rActive}赢了{\rDefault}？
```

#### 烧录
```bash
"$FFMPEG/ffmpeg.exe" -i video.mp4 -vf "ass=subtitle.ass" -c:v libx264 -crf 21 output.mp4
```

#### 自动方案
```bash
pip install tiktok-karaoke-captions
karaoke-captions video.mp4 --output styled_output.mp4
```

### 12.3 一键出片脚本

一个 JSON 配置 → 一条命令 → 4 阶段全自动出片。

#### 配置 `oneclick.json`
```json
{
  "project": "终宋_宣发",
  "media_dir": "E:/BaiduNetdiskDownload/05.终宋（76集）陈外＆王涵",
  "style": "cold_suspense",
  "bgm": "clips/ambient.wav",
  "bgm_vol": 0.3,
  "xfade_dur": 0.3,
  "speed": 1.03,
  "cover_text": "他一人一剑，守住最后一道城门",
  "clips": [
    {"source": "第73集.mp4", "start": 58, "dur": 6, "label": "对峙"},
    {"source": "第67集.mp4", "start": 22, "dur": 9, "label": "桥头七人对峙"},
    {"source": "第66集.mp4", "start": 5,  "dur": 12, "label": "行军打斗"},
    {"source": "第73集.mp4", "start": 28, "dur": 9, "label": "武士激战"},
    {"source": "第73集.mp4", "start": 80, "dur": 8, "label": "持剑独立"}
  ]
}
```

#### 一键出片脚本 `scripts/oneclick.sh`

```bash
#!/bin/bash
set -e
CONFIG="${1:-oneclick.json}"
FF="<ffmpeg路径>/ffmpeg.exe"
W=1080; H=1920; FPS=30

eval $(node -e "const c=JSON.parse(require('fs').readFileSync('$CONFIG','utf-8'));
  console.log('PROJECT='+c.project); console.log('MEDIA_DIR='+c.media_dir);
  console.log('STYLE='+(c.style||'warm')); console.log('BGM='+c.bgm);
  console.log('XFADE='+(c.xfade_dur||0.3)); console.log('SPEED='+(c.speed||1.0));
  console.log('COVER_TEXT='+c.cover_text);")

DIR="$MEDIA_DIR/${PROJECT}_output"
mkdir -p "$DIR"/{clips,edited,final}

# 调色预设
case "$STYLE" in
  warm) COLOR="eq=contrast=1.1:saturation=1.2,colorbalance=rs=0.08:gs=-0.03:bs=-0.10" ;;
  cold) COLOR="eq=contrast=1.15:brightness=-0.05:saturation=1.1,colorbalance=rs=-0.10:gs=0:bs=0.12" ;;
  high) COLOR="eq=contrast=1.25:saturation=1.4:gamma=1.1,curves=preset=strong_contrast" ;;
  *)    COLOR="eq=contrast=1.05" ;;
esac

AUDIO="highpass=f=70,equalizer=f=3000:t=q:w=1.5:g=3,\
deesser=i=0.5:f=6500:s=0:m=0,\
acompressor=threshold=-20dB:ratio=2.5:attack=5:release=50:makeup=3,\
loudnorm=I=-16:TP=-1.5:LRA=11"

echo "=== [1/4] 精确裁剪 ==="
node -e "
  const c=JSON.parse(require('fs').readFileSync('$CONFIG','utf-8'));
  c.clips.forEach((cl,i) => {
    const n=String(i+1).padStart(2,'0');
    console.log('$FF -y -ss '+cl.start+' -t '+cl.dur+
      ' -i $MEDIA_DIR/'+cl.source+
      ' -vf scale=$W:$H,fps=$FPS -crf 18 -preset fast'+
      ' -c:a aac -b:a 192k'+
      ' $DIR/clips/'+n+'_'+cl.label+'.mp4');
  });" | bash

echo "=== [2/4] 特效+调色+音频 ==="
for f in "$DIR"/clips/*.mp4; do
  name=$(basename "$f" .mp4)
  "$FF" -y -i "$f" \
    -vf "setpts=$(echo "1/$SPEED" | bc -l)*PTS,fps=$FPS,$COLOR" \
    -af "atempo=$SPEED,$AUDIO" \
    "$DIR/edited/${name}_edit.mp4"
done

echo "=== [3/4] xfade 组装 + BGM ==="
echo "file '$DIR/clips'/*.mp4" | sort > "$DIR/concat.txt"
# 使用 xfade_gen.js 生成完整 xfade 命令（§2 已有）
node "$SKILL_DIR/scripts/xfade_gen.js" $(for f in "$DIR"/edited/*_edit.mp4; do
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"; done)
# 混合 BGM
"$FF" -y -i "$DIR/assembled.mp4" -i "$BGM" -filter_complex \
  "[1:a]atrim=0:60,volume=0.3,afade=t=out:st=57:d=3[bgm];[0:a][bgm]amix=inputs=2:duration=first[aout]" \
  -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k "$DIR/final/${PROJECT}.mp4"

echo "=== [4/4] 封面+多平台 ==="
"$FF" -y -ss 2 -i "$DIR/final/${PROJECT}.mp4" -frames:v 1 -an \
  -vf "thumbnail,scale=$W:$H,drawtext=fontsize=64:fontcolor=white:\
       text='$COVER_TEXT':x=(w-text_w)/2:y=h-text_h-100:\
       box=1:boxcolor=black@0.6" "$DIR/final/${PROJECT}_cover.jpg"

"$FF" -y -i "$DIR/final/${PROJECT}.mp4" -t 60 -vf "scale=1080:1920" -crf 21 "$DIR/final/${PROJECT}_tiktok.mp4"
"$FF" -y -i "$DIR/final/${PROJECT}.mp4" -t 180 -vf "crop=ih*9/16:ih,scale=1920:1080" -crf 20 "$DIR/final/${PROJECT}_bilibili.mp4"

echo "✅ 一键出片完成: $DIR/final/"
ls -lh "$DIR/final/"
```

### 12.4 技能总览

```
oneclick.json（一个配置文件）
    ↓
§12.3 一键脚本
    ├── 阶段1: §8 独立裁剪（5 片段并行）
    ├── 阶段2: §9.3 变速 + §12.1 调色 + §11.2 音频精修
    ├── 阶段3: §2 xfade 组装 + §6 BGM 混音
    └── 阶段4: §11.1 封面 + §11.3 多平台导出
    ↓
TikTok / B站 / 微信 三版本同时输出
```

Sources:
- [FFmpeg lut3d color grading](https://mcpmarket.com/zh/tools/skills/ffmpeg-color-grading-chromakey)
- [ASS karaoke word-by-word](https://stackoverflow.com/questions/76848089)
- [tiktok-karaoke-captions](https://github.com/chjm-ai/tiktok-karaoke-captions)
- [viral-shorts-engine](https://github.com/abc-kkk/viral-shorts-engine)
