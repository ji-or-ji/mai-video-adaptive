"""端到端：视觉时间轴 + 音频时间轴同时产出。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

DS_KEY = "sk-be00893b96234425baae60b9148ef1ed"
MODEL = ROOT / "assets" / "models" / "model.int8.onnx"
TOKENS = ROOT / "assets" / "models" / "tokens.txt"
OUT = ROOT / "tools" / "_e2e_audio_out"


def main(name):
    v = ROOT / "assets" / name
    print(f"=== {name} ===")
    r = af.understand_video(
        v, out_dir=OUT / v.stem, asr_mode="local",
        local_model=MODEL, local_tokens=TOKENS,
        vision_api_key=DS_KEY, use_cache=False)
    print(f'帧数={r["frame_count"]}  耗时={r["elapsed"]:.1f}s\n')

    print("--- 视觉轨道 ---")
    for s in r["segments"]:
        print(f'{af.fmt_ts(s["start"])}~{af.fmt_ts(s["end"])}  {s["text"][:60]}')

    print("\n--- 音频轨道 ---")
    aseg = r.get("audio_segments") or []
    if not aseg:
        print("  (无)")
    for s in aseg:
        print(f'{af.fmt_ts(s["start"])}~{af.fmt_ts(s["end"])}  {s["text"][:60]}')

    print(f'\n--- 注入用摘要（{len(r["summary"])} 字符）---')
    print(r["summary"])


if __name__ == "__main__":
    for n in (sys.argv[1:] or ["speech_multi.mp4"]):
        main(n)
