"""
story_reader.py — 短剧对白读取与分析模块

1. 解析 ep01~ep76.vtt 为纯文本
2. 合并全部对白
3. 调用 DashScope qwen-plus 分析对白并输出 JSON
"""

import json
import os
import re
import sys
from openai import OpenAI

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
VTT_DIR = r"E:\视频\跑的数据\_work\_audio_cache"
EPISODE_COUNT = 76

# Output paths
MERGED_DIALOGUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_work", "merged_dialogue.txt")
GOLDEN_QUOTES_PATH = r"E:\视频\跑的数据\_work\golden_quotes.json"
STORY_PROFILE_PATH = r"E:\视频\跑的数据\_work\story_profile.json"

GOLDEN_QUOTES_PROMPT = """从以下短剧对白中提取所有金句/燃句/虐句/反转句，输出 JSON 数组。

每项包含：
- episode: 集数（如 "EP01"）
- timestamp: 时间戳（如 "00:30"）
- text: 原句
- type: 类型，取值为 "燃句" / "虐句" / "反转句" / "金句" / "钩子"
- impact: 冲击力评分 1-5（5最高）

要求：
- 只提取真正有冲击力、有记忆点的句子，宁缺毋滥
- 每项 text 应当是一句或一个连续语段，不要截断
- 请确保输出为合法 JSON 数组，不要用 markdown 代码块包裹"""

QUOTES_BATCH_SIZE = 25000


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_vtt(filepath: str, ep_label: str) -> str:
    """Parse a single VTT file into formatted dialogue lines.

    Returns lines like: [EP01 00:30] 对白内容
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Remove WEBVTT header
    content = re.sub(r"^WEBVTT\s*\n+", "", content)

    lines = []
    # Match timestamp blocks: each block is a timestamp line followed by text
    blocks = re.split(r"\n\n+", content.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # First line should be timestamp, rest is dialogue
        parts = block.split("\n", 1)
        timestamp_line = parts[0].strip()
        dialogue = parts[1].strip() if len(parts) > 1 else ""

        # Skip non-timestamp blocks (e.g. NOTE lines)
        if "-->" not in timestamp_line or not dialogue:
            continue

        # Extract start time, round to seconds: 00:00.400 -> 00:00
        match = re.match(r"(\d{2}):(\d{2})\.\d+", timestamp_line)
        if not match:
            continue
        mm, ss = match.group(1), match.group(2)

        lines.append(f"[{ep_label} {mm}:{ss}] {dialogue}")

    return "\n".join(lines)


def read_all_episodes(vtt_dir: str = VTT_DIR, episode_count: int = EPISODE_COUNT) -> str:
    """Read and merge all episode VTT files into a single text."""
    all_texts = []
    for ep in range(1, episode_count + 1):
        ep_str = f"ep{ep:02d}"
        vtt_path = os.path.join(vtt_dir, f"{ep_str}.vtt")
        if not os.path.exists(vtt_path):
            print(f"[WARN] 缺失: {vtt_path}", file=sys.stderr)
            continue
        ep_label = f"EP{ep:02d}"
        ep_text = parse_vtt(vtt_path, ep_label)
        if ep_text:
            all_texts.append(ep_text)
        print(f"[OK] 已读取 {ep_label} ({len(ep_text)} 字符)")

    merged = "\n\n".join(all_texts)
    print(f"\n[汇总] 共 {len(all_texts)} 集, 总计 {len(merged)} 字符")
    return merged


ANALYSIS_PROMPT = """你是一位短剧编剧。以下是古装权谋短剧《终宋》76集完整对白。请分析并输出 JSON：

{
  "剧名": "终宋",
  "类型": "古装权谋",
  "集数": 76,
  "剧情概要": "用300字概括全剧主线",
  "人物关系": [
    {
      "姓名": "角色名",
      "身份": "角色身份/阵营",
      "性格标签": ["标签1", "标签2"],
      "关键事件": "该角色的关键剧情"
    }
  ],
  "情节节点": [
    {
      "集数范围": "EP01-EP10",
      "阶段": "阶段名称",
      "概要": "该阶段主要剧情"
    }
  ],
  "情感弧线": "全剧情感走向分析 (200字)",
  "高潮段落": [
    {
      "位置": "EPXX 附近",
      "描述": "高潮内容描述"
    }
  ],
  "台词风格": "对白风格特点分析 (100字)",
  "改编建议": "三条可操作的改编/宣发建议"
}

要求：
- 人物关系至少列出 8 位主要角色
- 情节节点按集数范围划分，不少于 5 个阶段
- 高潮段落不少于 3 处
- 请确保输出为合法 JSON，不要用 markdown 代码块包裹"""


def analyze_with_dashscope(text: str, config: dict) -> dict:
    """Send merged dialogue to DashScope for analysis."""
    client = OpenAI(
        api_key=config["dashscope_api_key"],
        base_url=config["dashscope_base_url"],
    )

    model = config.get("text_model", "qwen-plus")

    print(f"\n[API] 正在调用 {model} 分析对白...")
    print(f"[API] 对白总长度: {len(text)} 字符")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一位专业的短剧编剧和分析师。请严格按照 JSON 格式回复，不要用 markdown 代码块包裹。"},
            {"role": "user", "content": ANALYSIS_PROMPT + "\n\n---\n以下是76集完整对白：\n\n" + text},
        ],
        temperature=0.3,
        max_tokens=16384,
    )

    raw = response.choices[0].message.content
    print(f"[API] 响应长度: {len(raw)} 字符")

    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)

    try:
        result = json.loads(raw)
        print("[API] JSON 解析成功")
        return result
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 解析失败: {e}", file=sys.stderr)
        print(f"[DEBUG] 原始响应前200字符: {raw[:200]}", file=sys.stderr)
        return {"raw_response": raw, "parse_error": str(e)}


def save_merged_text(text: str, output_path: str = None):
    """Save merged dialogue text to file."""
    if output_path is None:
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_work", "merged_dialogue.txt")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[SAVE] 已保存合并对白到: {output_path}")
    return output_path


def save_analysis(result: dict, output_path: str = None):
    """Save analysis result to JSON file."""
    if output_path is None:
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_work", "story_analysis.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[SAVE] 已保存分析结果到: {output_path}")
    return output_path


def extract_golden_quotes(config: dict, merged_path: str = None) -> list:
    """Extract golden quotes from merged dialogue using DashScope API.

    Reads merged dialogue, batches it, sends each batch to API,
    merges results and saves to golden_quotes.json and story_profile.json.
    """
    if merged_path is None:
        merged_path = MERGED_DIALOGUE_PATH

    # Read merged dialogue
    if not os.path.exists(merged_path):
        print(f"[ERROR] 合并对白文件不存在: {merged_path}", file=sys.stderr)
        return []

    with open(merged_path, "r", encoding="utf-8") as f:
        dialogue_text = f.read()

    print(f"[READ] 读取合并对白: {len(dialogue_text)} 字符")

    # Split into batches
    batches = []
    start = 0
    while start < len(dialogue_text):
        end = start + QUOTES_BATCH_SIZE
        # Try to break at a newline to avoid cutting mid-line
        if end < len(dialogue_text):
            nl = dialogue_text.rfind("\n", start, end)
            if nl > start + QUOTES_BATCH_SIZE // 2:
                end = nl + 1
        batches.append(dialogue_text[start:end])
        start = end

    print(f"[BATCH] 共分为 {len(batches)} 批 (每批 {QUOTES_BATCH_SIZE} 字)")

    client = OpenAI(
        api_key=config["dashscope_api_key"],
        base_url=config["dashscope_base_url"],
    )
    model = config.get("text_model", "qwen-plus")

    all_quotes = []

    for i, batch in enumerate(batches):
        print(f"\n[API] 批次 {i+1}/{len(batches)} — 长度 {len(batch)} 字符")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一位专业的短剧编剧和金句挖掘者。请严格按照 JSON 数组格式回复，不要用 markdown 代码块包裹。"},
                {"role": "user", "content": GOLDEN_QUOTES_PROMPT + "\n\n---\n以下是短剧对白片段：\n\n" + batch},
            ],
            temperature=0.3,
            max_tokens=16384,
        )

        raw = response.choices[0].message.content
        print(f"[API] 响应长度: {len(raw)} 字符")

        # Strip markdown code fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)

        try:
            batch_quotes = json.loads(raw)
            if isinstance(batch_quotes, list):
                print(f"[API] 批次 {i+1} 提取 {len(batch_quotes)} 条金句")
                all_quotes.extend(batch_quotes)
            else:
                print(f"[WARN] 批次 {i+1} 返回不是数组: {type(batch_quotes)}", file=sys.stderr)
        except json.JSONDecodeError as e:
            print(f"[ERROR] 批次 {i+1} JSON 解析失败: {e}", file=sys.stderr)
            print(f"[DEBUG] 前200字符: {raw[:200]}", file=sys.stderr)

    print(f"\n[汇总] 共提取 {len(all_quotes)} 条金句")

    # Save golden_quotes.json
    os.makedirs(os.path.dirname(GOLDEN_QUOTES_PATH), exist_ok=True)
    with open(GOLDEN_QUOTES_PATH, "w", encoding="utf-8") as f:
        json.dump(all_quotes, f, ensure_ascii=False, indent=2)
    print(f"[SAVE] 金句已保存到: {GOLDEN_QUOTES_PATH}")

    # Merge into story_profile.json
    if os.path.exists(STORY_PROFILE_PATH):
        with open(STORY_PROFILE_PATH, "r", encoding="utf-8") as f:
            profile = json.load(f)
    else:
        print(f"[WARN] story_profile.json 不存在，新建", file=sys.stderr)
        profile = {}

    profile["golden_quotes"] = all_quotes

    with open(STORY_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    print(f"[SAVE] story_profile.json 已更新 (golden_quotes 字段)")

    return all_quotes


def verify_golden_quotes(path: str = None) -> bool:
    """Verify golden_quotes.json is valid JSON."""
    if path is None:
        path = GOLDEN_QUOTES_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[VERIFY] {path} 合法 JSON, 包含 {len(data)} 条记录")
        # Show sample
        if data:
            print(f"[SAMPLE] {json.dumps(data[0], ensure_ascii=False)}")
        return True
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[FAIL] {path} 验证失败: {e}", file=sys.stderr)
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="短剧对白读取与分析")
    parser.add_argument("--mode", choices=["analyze", "quotes", "all"], default="all",
                        help="运行模式: analyze=剧情分析, quotes=金句提取, all=全部")
    parser.add_argument("--merged", default=None,
                        help="合并对白文件路径 (默认 _work/merged_dialogue.txt)")
    args = parser.parse_args()

    config = load_config()
    print(f"[CONFIG] 项目: {config['project']['project_name']}")
    print(f"[CONFIG] 模型: {config.get('text_model', 'qwen-plus')}")

    if args.mode in ("analyze", "all"):
        # Step 1: Read all episodes
        merged = read_all_episodes()

        # Step 2: Save merged text
        save_merged_text(merged)

        # Step 3: Analyze with DashScope
        result = analyze_with_dashscope(merged, config)

        # Step 4: Save analysis
        save_analysis(result)

        # Step 5: Print summary
        if "剧情概要" in result:
            print(f"\n{'='*60}")
            print(f"剧情概要: {result.get('剧情概要', 'N/A')[:200]}...")
            print(f"人物数量: {len(result.get('人物关系', []))}")
            print(f"情节阶段: {len(result.get('情节节点', []))}")
            print(f"高潮段落: {len(result.get('高潮段落', []))}")

    if args.mode in ("quotes", "all"):
        print(f"\n{'='*60}")
        print("[PHASE] 金句提取模式")
        merged_path = args.merged or MERGED_DIALOGUE_PATH
        quotes = extract_golden_quotes(config, merged_path)
        if quotes:
            verify_golden_quotes()
        return quotes

    return None


if __name__ == "__main__":
    main()
