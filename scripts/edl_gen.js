#!/usr/bin/env node
/**
 * EDL (Edit Decision List) 生成器 — CMX3600 格式
 * 兼容 Adobe Premiere Pro / After Effects / DaVinci Resolve
 *
 * 用法:
 *   node edl_gen.js <config.json> [--fps 30] [--title "项目名"]
 *
 * config.json 格式:
 * [
 *   {
 *     "source": "第73集.mp4",  // 源文件名，卷名会从此提取
 *     "src_start": 58,         // 源文件中开始时间（秒）
 *     "src_dur": 6,            // 使用的时长（秒）
 *     "rec_start": 0,          // 时间轴上的位置（秒），考虑 xfade offset
 *     "label": "对峙"           // 注释标签
 *   }
 * ]
 *
 * 导入 Premiere 后如素材离线：全选 → 右键 → 链接媒体 → 指向源文件目录
 */

const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
let configFile = "";
let fps = 30;
let title = "Untitled";
let dropFrame = false;
let mediaDir = "";

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--fps" && args[i + 1]) fps = parseInt(args[++i]);
  else if (args[i] === "--title" && args[i + 1]) title = args[++i];
  else if (args[i] === "--drop") dropFrame = true;
  else if (args[i] === "--media-dir" && args[i + 1]) mediaDir = args[++i];
  else if (!configFile && !args[i].startsWith("--")) configFile = args[i];
}

if (!configFile) {
  console.error("用法: node edl_gen.js <config.json> [--fps 30] [--title 标题] [--drop]");
  process.exit(1);
}

const config = JSON.parse(fs.readFileSync(configFile, "utf-8"));

function secToTC(totalSec, fps, drop) {
  if (totalSec < 0) totalSec = 0;
  const totalFrames = Math.round(totalSec * fps);
  const ff = totalFrames % fps;
  const ss = Math.floor(totalFrames / fps) % 60;
  const mm = Math.floor(totalFrames / (fps * 60)) % 60;
  const hh = Math.floor(totalFrames / (fps * 3600));
  return `${String(hh).padStart(2,"0")}:${String(mm).padStart(2,"0")}:${String(ss).padStart(2,"0")}:${String(ff).padStart(2,"0")}`;
}

function calcTotalDur(config) {
  const last = config[config.length - 1];
  return last.rec_start + last.src_dur;
}

/**
 * 从中文文件名提取 ASCII 卷名（CMX3600 固定列宽格式只能用 ASCII）
 * "第73集.mp4" → "Ep73"
 * "第67集.mp4" → "Ep67"
 */
function makeReelName(source) {
  const base = source.replace(/\.[^.]+$/, "");  // 去扩展名
  const digits = base.match(/\d+/g);
  if (digits && digits.length > 0) {
    // 取第一组连续数字，如 "第73集" → "73"
    return "Ep" + digits[0];
  }
  // 降级: 移除所有非 ASCII 字符，截断
  const ascii = base.replace(/[^\x00-\x7F]/g, "").replace(/\s+/g, "_");
  return ascii.slice(0, 12) || "Clip";
}

// 生成 EDL
let edl = "";
edl += `TITLE: ${title}\n`;
edl += `FCM: ${dropFrame ? "DROP FRAME" : "NON-DROP FRAME"}\n\n`;

if (mediaDir) {
  edl += `* MEDIA DIR: ${mediaDir}\n\n`;
}

config.forEach((clip, idx) => {
  const num = String(idx + 1).padStart(3, "0");
  // ★ 卷名必须纯 ASCII — CMX3600 用固定字节列位置，UTF-8 中文会破坏对齐
  const reel = makeReelName(clip.source);
  // CMX3600 reel 字段: 第 7-38 列 = 32 字符
  const reelCol = reel.padEnd(32);
  const srcIn = secToTC(clip.src_start, fps, dropFrame);
  const srcOut = secToTC(clip.src_start + clip.src_dur, fps, dropFrame);
  const recIn = secToTC(clip.rec_start, fps, dropFrame);
  const recOut = secToTC(clip.rec_start + clip.src_dur, fps, dropFrame);

  // CMX3600 格式: 事件号(1-3) + 2空格 + 卷名(32) + 空间距(6) + V/A + ...
  edl += `${num}  ${reelCol} V     C        ${srcIn} ${srcOut} ${recIn} ${recOut}\n`;
  if (clip.label) edl += `* COMMENT: ${clip.label}\n`;
  edl += `* FROM CLIP NAME: ${clip.source}\n`;
  if (mediaDir) {
    edl += `* SOURCE FILE: ${mediaDir.replace(/\\/g,"/")}/${clip.source}\n`;
  }
  edl += `${num}  ${reelCol} A     C        ${srcIn} ${srcOut} ${recIn} ${recOut}\n`;
  edl += `* FROM CLIP NAME: ${clip.source}\n`;
  edl += "\n";
});

const totalDur = calcTotalDur(config);
edl += `*** TOTAL DURATION: ${secToTC(totalDur, fps, dropFrame)}\n`;
edl += `*** FPS: ${fps}${dropFrame ? " DROP FRAME" : ""}\n`;
edl += `*** CLIPS: ${config.length}\n`;

const outFile = path.join(path.dirname(configFile), "timeline.edl");
fs.writeFileSync(outFile, edl, "utf-8");

console.log(`✅ EDL: ${outFile}`);
console.log(`   ${secToTC(totalDur, fps, dropFrame)} · ${totalDur.toFixed(1)}s · ${fps}fps · ${config.length} clips`);
console.log(`\n📋 时间线:`);
config.forEach((clip, idx) => {
  const srcIn = secToTC(clip.src_start, fps, dropFrame);
  const srcOut = secToTC(clip.src_start + clip.src_dur, fps, dropFrame);
  const recIn = secToTC(clip.rec_start, fps, dropFrame);
  const recOut = secToTC(clip.rec_start + clip.src_dur, fps, dropFrame);
  console.log(`   ${String(idx+1).padEnd(3)} ${clip.source.padEnd(18)} ${srcIn} → ${srcOut}  ⏩  ${recIn} → ${recOut}  ${clip.label||""}`);
});
