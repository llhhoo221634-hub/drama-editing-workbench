"""daily_test.py — 每日快速验证: 3集采样 → V3 → 2类宣发"""
import subprocess, sys, os, time, json, re

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SKILL_DIR)

def run(cmd, timeout=600):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, (r.stdout or '') + (r.stderr or ''), time.time() - t0

def main():
    print("=" * 60)
    print("  每日验证测试")
    print("=" * 60)
    results = {}

    print("\n[1/3] V2 趋势采样 (EP1,38,73)...")
    rc, out, t = run([sys.executable, "multi_frame_sample.py", "--eps", "1,38,73", "--workers", "1"])
    ok = rc == 0 and '完成' in out
    results['v2'] = ok
    print(f"  {'PASS' if ok else 'FAIL'} ({t:.0f}s)")

    print("\n[2/3] V3 构建...")
    rc, out, t = run([sys.executable, "build_analysis_v3.py"])
    m = re.search(r'usable:\s*(\d+)', out)
    usable = int(m.group(1)) if m else 0
    results['v3'] = rc == 0
    print(f"  {'PASS' if rc == 0 else 'FAIL'} ({t:.0f}s) usable={usable}")

    print("\n[3/3] 分子宣发 dry-run (hook_clash)...")
    rc, out, t = run([sys.executable, "promo_cli.py", "--types", "hook_clash", "--xfade", "none", "--dry-run"])
    results['molecular'] = rc == 0
    print(f"  {'PASS' if rc == 0 else 'FAIL'} ({t:.0f}s)")

    all_ok = all(results.values())
    print(f"\n{'='*60}")
    print(f"  {'✓ 全部通过' if all_ok else '✗ 有失败项'}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
