#!/usr/bin/env node
/**
 * 生成 xfade 组装 ffmpeg 命令
 *
 * 用法:
 *   node xfade_gen.js <d1> <d2> <d3> ...  (各片段时长，秒)
 *
 * 示例:
 *   node xfade_gen.js 6 9 12 9 8
 *   输出完整的 ffmpeg xfade 命令
 */

const durations = process.argv.slice(2).map(Number);
const fadeDur = 0.3;
const fps = 30;

if (durations.length < 2) {
  console.error("用法: node xfade_gen.js <片段1秒数> <片段2秒数> ...");
  console.error("示例: node xfade_gen.js 6 9 12 9 8");
  process.exit(1);
}

// 计算每个片段的 offset
let offset = 0;
const offsets = [0];
for (let i = 0; i < durations.length - 1; i++) {
  offset += durations[i] - fadeDur;
  offsets.push(Math.round(offset * 10) / 10);
}

const totalDur = Math.round((offsets[durations.length - 1] + durations[durations.length - 1]) * 10) / 10;

// 生成 filter_complex
let fi = "";
let inputs = "";

for (let i = 0; i < durations.length; i++) {
  inputs += ` -i t${String(i+1).padStart(2,'0')}.mp4`;
}

fi += `  -filter_complex "\\\n`;

// 视频：scale + fps
for (let i = 0; i < durations.length; i++) {
  fi += `    [${i}:v]scale=1080:1920:flags=lanczos,fps=${fps}[v${i}];\\\n`;
}

// 视频：xfade 链
let prevV = "v0";
for (let i = 1; i < durations.length; i++) {
  const outV = i === durations.length - 1 ? "vout" : `v${prevV}${i}`;
  if (i === 1) {
    fi += `    [v0][v1]xfade=transition=fade:duration=${fadeDur}:offset=${offsets[1]}[v01];\\\n`;
  } else {
    const lastOut = i === durations.length - 1 ? "vout" : `v${Array.from({length: i}, (_,k) => k).join('')}${i}`;
    // use last chain output
    const prevChain = Array.from({length: i}, (_,k) => k).join('');
    const prevOut = i === 2 ? "v01" : `v${prevChain}${i-1}`;
    const thisOut = i === durations.length - 1 ? "vout" : `v${prevChain}${i}`;
    fi += `    [${prevOut}][v${i}]xfade=transition=fade:duration=${fadeDur}:offset=${offsets[i]}[${thisOut}];\\\n`;
  }
}

// 音频
for (let i = 0; i < durations.length; i++) {
  fi += `    [${i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a${i}];\\\n`;
}
let prevA = "a0";
for (let i = 1; i < durations.length; i++) {
  const outA = i === durations.length - 1 ? "aout" : `a${prevA}${i}`;
  if (i === 1) {
    fi += `    [a0][a1]acrossfade=d=${fadeDur}[a01];\\\n`;
  } else {
    const prevChain = Array.from({length: i}, (_,k) => k).join('');
    const prevOut = i === 2 ? "a01" : `a${prevChain}${i-1}`;
    const thisOut = i === durations.length - 1 ? "aout" : `a${prevChain}${i}`;
    fi += `    [${prevOut}][a${i}]acrossfade=d=${fadeDur}[${thisOut}];\\\n`;
  }
}

fi += `  " \\\n`;
fi += `  -map "[vout]" -map "[aout]" \\\n`;
fi += `  -c:v libx264 -preset medium -crf 21 -pix_fmt yuv420p \\\n`;
fi += `  -c:a aac -b:a 192k -movflags +faststart \\\n`;
fi += `  trailer_assembled.mp4`;

console.log(`# ${durations.length} 片段, 总时长约 ${totalDur} 秒`);
console.log(`# xfade offsets: ${offsets.join(', ')}`);
console.log();
console.log(`ffmpeg -y${inputs} \\`);
console.log(fi);
