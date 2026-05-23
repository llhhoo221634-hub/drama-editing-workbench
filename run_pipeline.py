"""run_pipeline.py — 一键启动全流程: V3构建 → 6类分子宣发生成"""
import subprocess, sys, os, time

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SKILL_DIR)

def step(name, cmd):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    t0 = time.time()
    r = subprocess.run(cmd, shell=False)
    if r.returncode != 0:
        print(f"\n[FAIL] {name} 失败 (rc={r.returncode})")
        sys.exit(1)
    print(f"  耗时: {time.time() - t0:.0f}s")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--quick', action='store_true')
    p.add_argument('--xfade', default='fade')
    p.add_argument('--types', default='')
    p.add_argument('--duration', '-d', type=int, default=None,
                   help='用户指定目标时长（秒），覆盖模板默认 target_dur')
    args = p.parse_args()

    types = args.types or 'hook_clash,identity_twist,emotional_resonance,quote_rhythm,cinematic_beauty,suspense_hook'
    quick_flag = '--quick' if args.quick else ''
    xfade = args.xfade
    dur_flag = f'--duration {args.duration}' if args.duration else ''

    step("V3 决策构建", [sys.executable, "build_analysis_v3.py"])
    cmd = [sys.executable, "promo_cli.py", "--types", types, "--xfade", xfade]
    if args.quick:
        cmd.append("--quick")
    if args.duration:
        cmd.extend(["--duration", str(args.duration)])
    step("分子宣发生成", cmd)

    print(f"\n{'='*60}")
    print(f"  全流程完成! 视频在: _work/_work_molecular/")
    print(f"{'='*60}")
