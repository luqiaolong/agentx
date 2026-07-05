import time
from pathlib import Path
from PIL import ImageGrab

ROOT = Path(r"d:\java\agentprojects\agentx")
out_dir = ROOT / "scripts" / "_verify_taskbar"
out_dir.mkdir(parents=True, exist_ok=True)

time.sleep(4)
screen = ImageGrab.grab()
screen.save(out_dir / "fullscreen.png")
w, h = screen.size

taskbar = screen.crop((0, h - 60, w, h))
taskbar.save(out_dir / "taskbar.png")

BRAND = (79, 70, 229)
def dist(c1, c2):
    return sum((a-b)**2 for a, b in zip(c1, c2)) ** 0.5

brand_taskbar = sum(1 for x in range(w) for y in range(h - 60, h) if dist(screen.getpixel((x, y)), BRAND) < 80)
print(f"Taskbar brand-600 pixels: {brand_taskbar}")
print(f"Saved to: {out_dir}")
