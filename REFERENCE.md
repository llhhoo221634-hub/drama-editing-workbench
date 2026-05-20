# REFERENCE.md — ffmpeg 完整命令参考

> SKILL.md 的补充文档。包含所有 ffmpeg 命令的完整参数、格式对比表、ASS 模板等细节。

---

## 片段切割（完整）

### 无损切割
```bash
"$FFMPEG" -y -ss <开始秒> -t <时长秒> -i "第XX集.mp4" -c copy output.mp4
```

### 精确切割（帧级精度）
```bash
"$FFMPEG" -y -ss <开始秒> -t <时长秒> -i "第XX集.mp4" \
  -c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k output.mp4
```

### 批量切割
```bash
"$FFMPEG" -y -ss 10 -t 8 -i "第08集.mp4" -c copy cut_01.mp4
"$FFMPEG" -y -ss 45 -t 12 -i "第08集.mp4" -c copy cut_02.mp4
"$FFMPEG" -y -ss 90 -t 6 -i "第08集.mp4" -c copy cut_03.mp4
```

### 去头去尾
```bash
"$FFMPEG" -y -ss 3 -to $(ffprobe -v error -show_entries format=duration -of csv=p=0 input.mp4 | awk '{print $1-5}') -i input.mp4 -c copy output.mp4
```

---

## 合并拼接（完整）

### 混合格式拼接（不同分辨率/编码 → 统一后拼接）
```bash
for f in *.mp4; do
  "$FFMPEG" -y -i "$f" \
    -vf "scale=1080:1920:flags=lanczos,fps=30" \
    -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p \
    -c:a aac -b:a 192k -ar 44100 -ac 2 \
    "norm_$f"
done
```

### xfade 多片段转场组接（完整命令）
```bash
"$FFMPEG" -i c1.mp4 -i c2.mp4 -i c3.mp4 -filter_complex \
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

---

## 创意混剪（完整）

### 画中画位置变体
```bash
# 右下角
overlay=W-w-20:H-h-20
# 左上角
overlay=20:20
# 右上角
overlay=W-w-20:20
```

### 左右分屏
```bash
"$FFMPEG" -y -i left.mp4 -i right.mp4 -filter_complex \
  "[0:v]crop=iw/2:ih:0:0,scale=540:1920[l]; \
   [1:v]crop=iw/2:ih:ow:0,scale=540:1920[r]; \
   [l][r]hstack" output.mp4
```

### 变速完整参数
```bash
# 2倍速
setpts=0.5*PTS + atempo=2
# 慢放0.5倍
setpts=2*PTS + atempo=0.5
# 1.5倍速
setpts=0.666*PTS + atempo=1.5
```

### 定格帧（冻结最后一帧 N 秒）
```bash
"$FFMPEG" -y -i input.mp4 \
  -filter_complex "tpad=stop_mode=clone:stop_duration=3" \
  -c:v libx264 -preset fast -crf 21 -c:a copy output.mp4
```

---

## 音画处理（完整）

### 提取音频（三种格式）
```bash
# MP3
"$FFMPEG" -y -i input.mp4 -vn -acodec libmp3lame -b:a 192k output.mp3
# AAC
"$FFMPEG" -y -i input.mp4 -vn -acodec aac -b:a 192k output.aac
# WAV（无损）
"$FFMPEG" -y -i input.mp4 -vn -acodec pcm_s16le output.wav
```

### 横竖转换
```bash
# 横屏16:9 → 竖屏9:16（居中裁剪）
"$FFMPEG" -y -i input.mp4 \
  -vf "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos" output.mp4

# 竖屏9:16 → 横屏16:9
"$FFMPEG" -y -i input.mp4 \
  -vf "crop=iw*9/16:iw,scale=1920:1080:flags=lanczos" output.mp4
```

### 视频压缩
```bash
# 降低码率
"$FFMPEG" -y -i input.mp4 -c:v libx264 -preset medium -crf 28 -c:a aac -b:a 128k output.mp4
# 降低分辨率
"$FFMPEG" -y -i input.mp4 -vf "scale=720:1280:flags=lanczos" -crf 23 output.mp4
```

### 静音段删除
```bash
"$FFMPEG" -y -i input.mp4 \
  -af "silenceremove=stop_periods=-1:stop_duration=1.5:stop_threshold=-40dB" \
  -c:v copy -c:a aac output.mp4
```

### BGM 混合（完整参数）
```bash
"$FFMPEG" -y -i video.mp4 -i bgm.wav -filter_complex \
  "[1:a]atrim=0:60,volume=0.3,afade=t=out:st=57:d=3[bgm]; \
   [0:a][bgm]amix=inputs=2:duration=first[amix]; \
   [amix]afade=t=out:st=57:d=3[aout]" \
  -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k output.mp4
```

---

## Premiere/AE 导出（完整）

### 母版格式对比

| 格式 | 比特率 | 画质 | 文件大小(42s) | 适用 |
|------|--------|------|--------------|------|
| DNxHR HQ | ~220 Mbps | 优秀 | ~1.2 GB | Windows/Premiere |
| ProRes 422 HQ | ~280 Mbps | 优秀 | ~1.5 GB | Mac/Premiere/AE |
| ProRes 4444 | ~350 Mbps | 无损 | ~1.8 GB | AE 特效合成 |
| H.264 CRF18 | ~15 Mbps | 很好 | ~80 MB | 预览/交付 |

### DNxHR HQ（Windows 推荐）
```bash
"$FFMPEG" -y -i input.mp4 \
  -c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p \
  -c:a pcm_s16le -ar 48000 output_DNxHR.mov
```

### ProRes 422 HQ（Mac 推荐）
```bash
"$FFMPEG" -y -i input.mp4 \
  -c:v prores_ks -profile:v 3 -pix_fmt yuv422p10le \
  -c:a pcm_s16le -ar 48000 output_ProRes.mov
```

### ProRes 4444（含 Alpha，AE 合成）
```bash
"$FFMPEG" -y -i input.mp4 \
  -c:v prores_ks -profile:v 4 -pix_fmt yuva444p10le \
  -c:a pcm_s16le -ar 48000 output_ProRes4444.mov
```

### 分轨音频导出
```bash
# 对白轨
"$FFMPEG" -y -i assembled.mp4 -vn -acodec pcm_s16le -ar 48000 对白轨.wav
# BGM轨
"$FFMPEG" -y -i bgm.wav -acodec pcm_s16le -ar 48000 BGM轨.wav
```

---

## 调色预设（完整参数）

| 风格 | 滤镜链 | 适用剧种 |
|------|--------|---------|
| 暖调甜宠 | `eq=contrast=1.1:brightness=0.03:saturation=1.2,colorbalance=rs=0.08:gs=-0.03:bs=-0.10` | 甜宠、古装言情 |
| 冷调悬疑 | `eq=contrast=1.15:brightness=-0.05:saturation=1.1,colorbalance=rs=-0.10:gs=0:bs=0.12` | 悬疑、虐恋 |
| 高饱和爽剧 | `eq=contrast=1.25:brightness=0.02:saturation=1.4:gamma=1.1,curves=preset=strong_contrast` | 逆袭、战神 |

### LUT 文件调色
```bash
# 直接应用
"$FFMPEG" -i input.mp4 -vf "lut3d=look.cube" output.mp4

# 50% 强度叠加
"$FFMPEG" -i input.mp4 -filter_complex \
  "[0:v]split[v1][v2];[v1]lut3d=look.cube[lut]; \
   [v2][lut]blend=all_mode=overlay:all_opacity=0.5" output.mp4
```

---

## 音频精修（完整）

### 两段式 loudnorm（更精确）
```bash
# 第一遍：测量
"$FFMPEG" -i input.mp4 -af "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json" -f null -

# 第二遍：用测量值修正
"$FFMPEG" -i input.mp4 \
  -af "loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=-20.5:measured_TP=-3.2:\
       measured_LRA=7.5:measured_thresh=-30.5:offset=1.5:linear=true" \
  -c:v copy output.mp4
```

---

## ASS 卡拉OK字幕模板

```ass
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Style: Default,Microsoft YaHei,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,80,1
Style: Active,Microsoft YaHei,56,&H0000deff,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,80,1

[Events]
Dialogue: 0,0:00:00.00,0:00:06.00,Default,,0,0,0,,{\rActive}你{\rDefault}以为你赢了？
Dialogue: 0,0:00:01.50,0:00:06.00,Default,,0,0,0,,你{\rActive}以为{\rDefault}你赢了？
Dialogue: 0,0:00:02.30,0:00:06.00,Default,,0,0,0,,你以为你{\rActive}赢了{\rDefault}？
```

---

## ffmpeg 特效命令（完整）

### 闪白转场
```bash
"$FFMPEG" -i input.mp4 -filter_complex \
  "color=white:s=1080x1920:d=0.1,format=rgba[flash]; \
   [0:v][flash]overlay=enable='between(t,2,2.1)'" output.mp4
```

### 变速呼吸感（0.95x→1.0x→1.05x）
```bash
"$FFMPEG" -i input.mp4 \
  -vf "setpts=0.95*PTS,fps=30" -af "atempo=1.05" output.mp4
```

### 推进放大（zoom in）
```bash
"$FFMPEG" -i input.mp4 -vf \
  "scale=4320:7680,zoompan=z='min(max(zoom,pzoom)+0.0005,1.15)':d=1:\
   x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30" output.mp4
```

### 震屏效果
```bash
"$FFMPEG" -i input.mp4 -vf \
  "crop=iw-30:ih-30:15+15*sin(2*PI*30*t)*between(t,1,1.3):\
   15+15*cos(2*PI*30*t)*between(t,1,1.3),scale=1080:1920" output.mp4
```

---

## 音效设计参考

| 场景 | 音效 | ffmpeg 参数 |
|------|------|------------|
| 转场 | 短促"咔""咻" | 叠加 0.1s 白噪 |
| 悬念 | 心跳+滴答 | `-af "equalizer=f=50:t=q:w=1:g=5"` |
| 爽点 | 金属撞击/爆炸 | BGM 卡点燃曲 |
| 沉默 | 高潮后突然静音 | `-af "volume=enable='between(t,5,6)':volume=0"` |

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

---

## 7种情绪钩子模板

| 类型 | 句式 | 适用 |
|------|------|------|
| 疑问挑战 | "你敢信？……" | 反常识 |
| 反常识宣言 | "我被骂了三年，但今天必须说出真相" | 情感 |
| 场景痛点 | "凌晨三点的急诊室，护士突然递给我一张纸条……" | 剧情 |
| 数字冲击 | "从月薪3千到5万，我只做了这1件事" | 职场 |
| 身份代入 | "所有女生！""二胎妈妈的凌晨崩溃" | 美妆/亲子 |
| 悬念前置 | "最后一步做错，前面全白费" | 教程 |
| 视觉暴力 | 慢镜头+特效+强音效 | 动作 |

---

## 去字幕方案（附录）

实测结论（终宋）：
- 字幕位置每部剧不同（终宋在画面 68%-73% 处），必须先 vision.js 定位
- ffmpeg `delogo` 在 Gyan Windows build 有 bug，不可用
- ffmpeg `boxblur` 会产生模糊带，不自然
- AI LaMa 羽化遮罩（GaussianBlur 15px）+ IOPaint 可无痕擦除
- 处理全片需约 30 分钟（3653 帧）
- 工具链：IOPaint + LaMa 模型 + 羽化遮罩 + Python 并行（2 worker）

---

## 一键出片脚本完整版（scripts/oneclick.sh）

```bash
#!/bin/bash
set -e
CONFIG="${1:-oneclick.json}"
FF="$(node -e "console.log(require('./config.json').ffmpeg)")"
W=1080; H=1920; FPS=30

eval $(node -e "const c=JSON.parse(require('fs').readFileSync('$CONFIG','utf-8'));
  console.log('PROJECT='+c.project); console.log('MEDIA_DIR='+c.media_dir);
  console.log('STYLE='+(c.style||'warm')); console.log('BGM='+c.bgm);
  console.log('XFADE='+(c.xfade_dur||0.3)); console.log('SPEED='+(c.speed||1.0));
  console.log('COVER_TEXT='+c.cover_text);")

DIR="$MEDIA_DIR/${PROJECT}_output"
mkdir -p "$DIR"/{clips,edited,final}

case "$STYLE" in
  warm*) COLOR="eq=contrast=1.1:saturation=1.2,colorbalance=rs=0.08:gs=-0.03:bs=-0.10" ;;
  cold*) COLOR="eq=contrast=1.15:brightness=-0.05:saturation=1.1,colorbalance=rs=-0.10:gs=0:bs=0.12" ;;
  high*) COLOR="eq=contrast=1.25:saturation=1.4:gamma=1.1,curves=preset=strong_contrast" ;;
  *)     COLOR="eq=contrast=1.05" ;;
esac

AUDIO="highpass=f=70,equalizer=f=3000:t=q:w=1.5:g=3,deesser=i=0.5:f=6500:s=0:m=0,acompressor=threshold=-20dB:ratio=2.5:attack=5:release=50:makeup=3,loudnorm=I=-16:TP=-1.5:LRA=11"

echo "=== [1/4] 精确裁剪 ==="
node -e "
  const c=JSON.parse(require('fs').readFileSync('$CONFIG','utf-8'));
  c.clips.forEach((cl,i) => {
    const n=String(i+1).padStart(2,'0');
    console.log('\"$FF\" -y -ss '+cl.start+' -t '+cl.dur+
      ' -i \"$MEDIA_DIR/'+cl.source+'\"'+
      ' -vf \"scale=$W:$H,fps=$FPS\" -crf 18 -preset fast'+
      ' -c:a aac -b:a 192k'+
      ' \"$DIR/clips/'+n+'_'+cl.label+'.mp4\"');
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
node scripts/xfade_gen.js $(for f in "$DIR"/edited/*_edit.mp4; do
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"; done)
"$FF" -y -i "$DIR/assembled.mp4" -i "$BGM" -filter_complex \
  "[1:a]volume=0.3,afade=t=out:st=57:d=3[bgm];[0:a][bgm]amix=inputs=2:duration=first[aout]" \
  -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k "$DIR/final/${PROJECT}.mp4"

echo "=== [4/4] 封面+多平台 ==="
"$FF" -y -ss 2 -i "$DIR/final/${PROJECT}.mp4" -frames:v 1 -an \
  -vf "thumbnail,scale=$W:$H,drawtext=fontsize=64:fontcolor=white:\
       text='$COVER_TEXT':x=(w-text_w)/2:y=h-text_h-100:\
       box=1:boxcolor=black@0.6" "$DIR/final/${PROJECT}_cover.jpg"

"$FF" -y -i "$DIR/final/${PROJECT}.mp4" -t 60 -vf "scale=1080:1920" -crf 21 "$DIR/final/${PROJECT}_tiktok.mp4"
"$FF" -y -i "$DIR/final/${PROJECT}.mp4" -t 180 -vf "crop=ih*9/16:ih,scale=1920:1080" -crf 20 "$DIR/final/${PROJECT}_bilibili.mp4"

echo "Done: $DIR/final/"
ls -lh "$DIR/final/"
```

---

## 多平台导出参数

```bash
# TikTok/抖音：1080x1920, ≤60s
"$FFMPEG" -y -i input.mp4 -t 60 -vf "scale=1080:1920" -crf 21 output_tiktok.mp4

# 微信视频号：720x1280, ≤30s
"$FFMPEG" -y -i input.mp4 -t 30 -vf "scale=720:1280" -crf 21 output_wechat.mp4

# B站：1920x1080（竖屏居中裁剪）, ≤3min
"$FFMPEG" -y -i input.mp4 -t 180 -vf "crop=ih*9/16:ih,scale=1920:1080" -crf 20 output_bilibili.mp4
```
