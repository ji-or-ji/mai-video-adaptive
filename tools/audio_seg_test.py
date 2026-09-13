"""验证 audio_transcript 的时间戳输出（分块转录）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adaptive_frames as af  # noqa: E402

MODEL = ROOT / "assets" / "models" / "model.int8.onnx"
TOKENS = ROOT / "assets" / "models" / "tokens.txt"
OUT = ROOT / "tools" / "_audio_seg_out"


def run(name, mode="local"):
    v = ROOT / "assets" / name
    print("=" * 62)
    print(f"{name}  mode={mode}")
    r = af.audio_transcript(
        v, out_dir=OUT / v.stem, mode=mode,
        local_model=MODEL if mode == "local" else None,
        local_tokens=TOKENS if mode == "local" else None,
        block_s=60.0)
    print(f'  used={r.get("used")} skipped={r.get("skipped")} '
          f'reason={r.get("reason")} ratio={r.get("speech_ratio")}')
    segs = r.get("segments") or []
    print(f"  分块 {len(segs)} 段")
    for s in segs:
        print(f'  [{af.fmt_ts(s["start"])}~{af.fmt_ts(s["end"])}] {s["text"]}')
    print(f'  全文：{str(r.get("text") or "")[:120]}')


if __name__ == "__main__":
    names = sys.argv[1:] or ["speech_multi.mp4"]
    for n in names:
        run(n)
