#!/usr/bin/env node
/**
 * FCP 7 XML (XMEML) 生成器 — 精确匹配 Adobe Premiere Pro 导出格式
 * 兼容 Premiere Pro 2025 / After Effects 2025 / DaVinci Resolve
 *
 * 用法:
 *   node fcp_xml_gen.js <config.json> [--fps 30] [--title "项目名"]
 *
 * ★ 格式严格对标 Premiere Pro File→Export→Final Cut Pro XML 输出
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const args = process.argv.slice(2);
let configFile = args.find(a => !a.startsWith("--")) || "";
const getArg = (name, def) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i+1] ? args[i+1] : def;
};

if (!configFile || !fs.existsSync(configFile)) {
  console.error("用法: node fcp_xml_gen.js <config.json> [--fps 30] [--title 标题]");
  process.exit(1);
}

const cfg = JSON.parse(fs.readFileSync(configFile, "utf-8"));
const fps = parseInt(getArg("--fps", cfg.fps || 30));
const title = getArg("--title", cfg.title || "Sequence");
const width = cfg.width || 1080;
const height = cfg.height || 1920;
const xfadeDur = cfg.xfade_dur || 0.3;
const clips = cfg.clips || [];
const mediaDir = cfg.media_dir || path.dirname(configFile);
const normalizedMediaDir = mediaDir.replace(/\\/g, "/").replace(/\/+$/, "");
const ntsc = (fps === 30 || fps === 29.97) ? "true" : "false";

function secToFrames(sec) { return Math.round(sec * fps); }

function secToTC(sec) {
  const f = secToFrames(sec);
  const ff = f % fps;
  const ss = Math.floor(f / fps) % 60;
  const mm = Math.floor(f / (fps * 60)) % 60;
  const hh = Math.floor(f / (fps * 3600));
  return `${String(hh).padStart(2,"0")}:${String(mm).padStart(2,"0")}:${String(ss).padStart(2,"0")}:${String(ff).padStart(2,"0")}`;
}

function esc(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function makeFileUrl(filename) {
  const fullPath = `${normalizedMediaDir}/${filename}`;
  return `file:///${encodeURI(fullPath).replace(/^\/+/, "")}`;
}

// ── 探测源文件实际时长 ──
const ffprobePaths = [
  "C:/Users/Administrator/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-full_build/bin/ffprobe.exe",
  "ffprobe", "ffprobe.exe"
];
let ffprobeBin = null;
for (const fp of ffprobePaths) {
  try { execSync(`"${fp}" -version`, { stdio: "pipe" }); ffprobeBin = fp; break; } catch(e) {}
}

const sourceFileDurations = {};
const uniqueSources = [...new Set(clips.map(c => c.source))];

for (const src of uniqueSources) {
  const fullPath = `${mediaDir}/${src}`.replace(/\\/g, "/");
  if (ffprobeBin && fs.existsSync(fullPath)) {
    try {
      const out = execSync(
        `"${ffprobeBin}" -v error -show_entries format=duration -of csv=p=0 "${fullPath}"`,
        { encoding: "utf-8", timeout: 10000 }
      );
      const durSec = parseFloat(out.trim());
      if (durSec > 0) {
        sourceFileDurations[src] = Math.round(durSec * fps);
        console.log(`   📐 ${src}: ${durSec.toFixed(1)}s → ${sourceFileDurations[src]} frames`);
      }
    } catch(e) {
      console.error(`   ⚠️ 无法探测 ${src}: ${e.message}`);
    }
  }
  if (!sourceFileDurations[src]) {
    const maxOut = Math.max(...clips.filter(c => c.source === src).map(c => secToFrames(c.src_start + c.src_dur)));
    sourceFileDurations[src] = maxOut + 1;
    console.log(`   ⚠️ ${src}: 使用估算值 ${sourceFileDurations[src]} frames`);
  }
}

// 同源文件共享 file id
const sourceToFileId = {};
let nextFileId = 1;
for (const src of uniqueSources) { sourceToFileId[src] = `file-${nextFileId++}`; }

// 时间轴位置（xfade 重叠）
const recOffsets = [0];
for (let i = 1; i < clips.length; i++) {
  recOffsets.push(Math.round((recOffsets[i-1] + clips[i-1].src_dur - xfadeDur) * 1000) / 1000);
}
const totalDur = recOffsets[clips.length-1] + clips[clips.length-1].src_dur;

// ── 公共辅助函数 ──
function timecodeElement(srcStartFrames, srcStartSec) {
  return [
    `              <timecode>`,
    `                <string>${secToTC(srcStartSec)}</string>`,
    `                <frame>${srcStartFrames}</frame>`,
    `                <displayformat>NDF</displayformat>`,
    `                <rate>`,
    `                  <timebase>${fps}</timebase>`,
    `                  <ntsc>${ntsc}</ntsc>`,
    `                </rate>`,
    `              </timecode>`
  ].join("\n");
}

function videoMediaElement(fileDurFrames) {
  return [
    `                <video>`,
    `                  <duration>${fileDurFrames}</duration>`,
    `                  <samplecharacteristics>`,
    `                    <width>${width}</width>`,
    `                    <height>${height}</height>`,
    `                    <pixelaspectratio>square</pixelaspectratio>`,
    `                    <fielddominance>none</fielddominance>`,
    `                  </samplecharacteristics>`,
    `                </video>`
  ].join("\n");
}

function audioMediaElement(fileDurFrames) {
  return [
    `                <audio>`,
    `                  <duration>${fileDurFrames}</duration>`,
    `                  <samplecharacteristics>`,
    `                    <depth>16</depth>`,
    `                    <samplerate>48000</samplerate>`,
    `                  </samplecharacteristics>`,
    `                  <channelcount>2</channelcount>`,
    `                </audio>`
  ].join("\n");
}

// ── 生成 XML ──
let xml = `<?xml version="1.0" encoding="UTF-8"?>\n`;
xml += `<!DOCTYPE xmeml>\n`;
xml += `<xmeml version="5">\n`;
xml += `  <sequence id="sequence-1">\n`;
xml += `    <name>${esc(title)}</name>\n`;
xml += `    <duration>${secToFrames(totalDur)}</duration>\n`;
xml += `    <rate>\n`;
xml += `      <timebase>${fps}</timebase>\n`;
xml += `      <ntsc>${ntsc}</ntsc>\n`;
xml += `    </rate>\n`;
xml += `    <in>-1</in>\n`;
xml += `    <out>-1</out>\n`;
xml += `    <timecode>\n`;
xml += `      <string>00:00:00:00</string>\n`;
xml += `      <frame>0</frame>\n`;
xml += `      <displayformat>NDF</displayformat>\n`;
xml += `      <rate>\n`;
xml += `        <timebase>${fps}</timebase>\n`;
xml += `        <ntsc>${ntsc}</ntsc>\n`;
xml += `      </rate>\n`;
xml += `    </timecode>\n`;
xml += `    <media>\n`;
xml += `      <video>\n`;
xml += `        <track>\n`;

// ── 视频片段 ──
for (let i = 0; i < clips.length; i++) {
  const clip = clips[i];
  const srcStart = secToFrames(clip.src_start);
  const srcEnd = secToFrames(clip.src_start + clip.src_dur);
  const recStart = secToFrames(recOffsets[i]);
  const recEnd = secToFrames(recOffsets[i] + clip.src_dur);
  const clipDur = secToFrames(clip.src_dur);         // ★ 片段在时间轴的时长
  const fileDur = sourceFileDurations[clip.source];   // ★ 源文件完整时长
  const fileUrl = makeFileUrl(clip.source);
  const fileId = sourceToFileId[clip.source];

  xml += `          <clipitem id="clip-${i+1}">\n`;
  xml += `            <name>${esc(clip.source)}</name>\n`;
  xml += `            <duration>${clipDur}</duration>\n`;           // ★ = clip 时长
  xml += `            <rate>\n`;
  xml += `              <timebase>${fps}</timebase>\n`;
  xml += `              <ntsc>${ntsc}</ntsc>\n`;
  xml += `            </rate>\n`;
  xml += `            <start>${recStart}</start>\n`;
  xml += `            <end>${recEnd}</end>\n`;
  xml += `            <enabled>true</enabled>\n`;
  xml += `            <in>${srcStart}</in>\n`;
  xml += `            <out>${srcEnd}</out>\n`;
  if (clip.label) xml += `            <labels><label2>${esc(clip.label)}</label2></labels>\n`;
  xml += `            <file id="${fileId}">\n`;
  xml += `              <duration>${fileDur}</duration>\n`;          // ★ = 源文件完整时长
  xml += `              <rate>\n`;
  xml += `                <timebase>${fps}</timebase>\n`;
  xml += `                <ntsc>${ntsc}</ntsc>\n`;
  xml += `              </rate>\n`;
  xml += `              <name>${esc(clip.source)}</name>\n`;
  xml += `              <pathurl>${esc(fileUrl)}</pathurl>\n`;
  xml += timecodeElement(srcStart, clip.src_start) + "\n";
  xml += `              <media>\n`;
  xml += videoMediaElement(fileDur) + "\n";
  xml += audioMediaElement(fileDur) + "\n";
  xml += `              </media>\n`;
  xml += `            </file>\n`;
  xml += `          </clipitem>\n`;

  // 转场
  if (i < clips.length - 1) {
    const transFrames = secToFrames(xfadeDur);
    const transStart = recEnd - transFrames;
    xml += `          <transitionitem>\n`;
    xml += `            <start>${transStart}</start>\n`;
    xml += `            <end>${recEnd}</end>\n`;
    xml += `            <alignment>center</alignment>\n`;
    xml += `            <effect>\n`;
    xml += `              <name>Cross Dissolve</name>\n`;
    xml += `              <effectid>crossdissolve</effectid>\n`;
    xml += `              <effecttype>transition</effecttype>\n`;
    xml += `              <mediatype>video</mediatype>\n`;
    xml += `              <parameter authoringApp="PremierePro">\n`;
    xml += `                <parameterid>center</parameterid>\n`;
    xml += `                <value>0</value>\n`;
    xml += `              </parameter>\n`;
    xml += `            </effect>\n`;
    xml += `          </transitionitem>\n`;
  }
}

xml += `        </track>\n`;
// ★ format 在 track 之后（对标 Premiere 导出）
xml += `        <format>\n`;
xml += `          <samplecharacteristics>\n`;
xml += `            <width>${width}</width>\n`;
xml += `            <height>${height}</height>\n`;
xml += `            <pixelaspectratio>square</pixelaspectratio>\n`;
xml += `            <fielddominance>none</fielddominance>\n`;
xml += `            <rate>\n`;
xml += `              <timebase>${fps}</timebase>\n`;
xml += `              <ntsc>${ntsc}</ntsc>\n`;
xml += `            </rate>\n`;
xml += `          </samplecharacteristics>\n`;
xml += `        </format>\n`;
xml += `      </video>\n`;
xml += `      <audio>\n`;
xml += `        <track>\n`;

// ── 音频片段 ──
for (let i = 0; i < clips.length; i++) {
  const clip = clips[i];
  const srcStart = secToFrames(clip.src_start);
  const srcEnd = secToFrames(clip.src_start + clip.src_dur);
  const recStart = secToFrames(recOffsets[i]);
  const recEnd = secToFrames(recOffsets[i] + clip.src_dur);
  const clipDur = secToFrames(clip.src_dur);
  const fileDur = sourceFileDurations[clip.source];
  const fileUrl = makeFileUrl(clip.source);
  const fileId = sourceToFileId[clip.source];

  xml += `          <clipitem id="aclip-${i+1}">\n`;
  xml += `            <name>${esc(clip.source)}</name>\n`;
  xml += `            <duration>${clipDur}</duration>\n`;
  xml += `            <rate>\n`;
  xml += `              <timebase>${fps}</timebase>\n`;
  xml += `              <ntsc>${ntsc}</ntsc>\n`;
  xml += `            </rate>\n`;
  xml += `            <start>${recStart}</start>\n`;
  xml += `            <end>${recEnd}</end>\n`;
  xml += `            <enabled>true</enabled>\n`;
  xml += `            <in>${srcStart}</in>\n`;
  xml += `            <out>${srcEnd}</out>\n`;
  xml += `            <file id="${fileId}">\n`;
  xml += `              <duration>${fileDur}</duration>\n`;
  xml += `              <rate>\n`;
  xml += `                <timebase>${fps}</timebase>\n`;
  xml += `                <ntsc>${ntsc}</ntsc>\n`;
  xml += `              </rate>\n`;
  xml += `              <name>${esc(clip.source)}</name>\n`;
  xml += `              <pathurl>${esc(fileUrl)}</pathurl>\n`;
  xml += timecodeElement(srcStart, clip.src_start) + "\n";
  xml += `              <media>\n`;
  xml += audioMediaElement(fileDur) + "\n";
  xml += `              </media>\n`;
  xml += `            </file>\n`;
  xml += `            <sourcetrack>\n`;
  xml += `              <mediatype>audio</mediatype>\n`;
  xml += `              <trackindex>1</trackindex>\n`;
  xml += `            </sourcetrack>\n`;
  xml += `          </clipitem>\n`;

  if (i < clips.length - 1) {
    const transFrames = secToFrames(xfadeDur);
    const transStart = recEnd - transFrames;
    xml += `          <transitionitem>\n`;
    xml += `            <start>${transStart}</start>\n`;
    xml += `            <end>${recEnd}</end>\n`;
    xml += `            <alignment>center</alignment>\n`;
    xml += `            <effect>\n`;
    xml += `              <name>Cross Fade (+3dB)</name>\n`;
    xml += `              <effectid>crossfade</effectid>\n`;
    xml += `              <effecttype>transition</effecttype>\n`;
    xml += `              <mediatype>audio</mediatype>\n`;
    xml += `            </effect>\n`;
    xml += `          </transitionitem>\n`;
  }
}

xml += `        </track>\n`;
xml += `      </audio>\n`;
xml += `    </media>\n`;
xml += `  </sequence>\n`;
xml += `</xmeml>\n`;

const outFile = path.join(path.dirname(configFile), "timeline.xml");
fs.writeFileSync(outFile, xml, "utf-8");

console.log(`\n✅ FCP XML: ${outFile}`);
console.log(`   ${totalDur.toFixed(1)}s · ${fps}fps · ${clips.length} clips · ${xfadeDur}s xfade × ${clips.length-1}`);
for (let i = 0; i < clips.length; i++) {
  const c = clips[i];
  console.log(`   ${i+1}. [${secToTC(recOffsets[i])}] ${c.source}  (源 ${secToTC(c.src_start)}→${secToTC(c.src_start+c.src_dur)})  ${c.label||""}`);
  if (i < clips.length - 1) console.log(`      ↳ ${xfadeDur}s Cross Dissolve`);
}
console.log(`\n💡 Premiere Pro: 文件 → 导入 → timeline.xml`);
console.log(`   After Effects: 文件 → 导入 → timeline.xml`);
