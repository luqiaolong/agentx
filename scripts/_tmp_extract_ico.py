from PIL import Image
from pathlib import Path

ROOT = Path(r"d:\java\agentprojects\agentx")
out_dir = ROOT / "scripts" / "_ico_frames"
out_dir.mkdir(parents=True, exist_ok=True)

ico = Image.open(ROOT / "build" / "icon.ico")
for i in range(ico.n_frames):
    ico.seek(i)
    frame = ico.copy()
    frame.save(out_dir / f"icon_frame_{i}_{frame.width}x{frame.height}.png")
    print(f"Frame {i}: {frame.width}x{frame.height}")
