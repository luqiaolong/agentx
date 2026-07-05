from PIL import Image
from pathlib import Path

ROOT = Path(r"d:\java\agentprojects\agentx")
BRAND = (79, 70, 229)

def dist(c1, c2):
    return sum((a-b)**2 for a, b in zip(c1, c2)) ** 0.5

for size in [16, 24, 32, 48, 64, 128, 256, 512]:
    path = ROOT / "build" / f"icon-{size}.png"
    img = Image.open(path).convert("RGBA")
    white_pixels = []
    for x in range(img.width):
        for y in range(img.height):
            p = img.getpixel((x, y))
            if p[3] > 128 and p[0] > 200 and p[1] > 200 and p[2] > 200:
                white_pixels.append((x, y))
    if white_pixels:
        xs = [p[0] for p in white_pixels]
        ys = [p[1] for p in white_pixels]
        w = max(xs) - min(xs) + 1
        h = max(ys) - min(ys) + 1
        print(f"icon-{size}.png: white bbox {w}x{h} at ({min(xs)},{min(ys)}) ratio={w/size:.2%} x {h/size:.2%}")
    else:
        print(f"icon-{size}.png: no white pixels")
