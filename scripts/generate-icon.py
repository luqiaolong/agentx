from PIL import Image, ImageDraw
import os
import sys

# 生成 AgentX 应用图标：圆角方块 + 机器人脸，与 UI 左上角风格保持一致
BG = (79, 70, 229)  # brand-600 (#4f46e5)
WHITE = (255, 255, 255)
RADIUS_RATIO = 48 / 256  # 圆角半径与尺寸的比例

BASE_SIZE = 512  # 最高分辨率
RADIUS = int(RADIUS_RATIO * BASE_SIZE)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    scale = size / BASE_SIZE
    radius = int(RADIUS_RATIO * size)

    # 圆角矩形背景
    draw.rounded_rectangle([0, 0, size, size], radius=radius, fill=BG)

    # 机器人脸：矩形脸 + 两个圆眼 + 天线
    face_left = int(64 * scale)
    face_top = int(72 * scale)
    face_right = int(192 * scale)
    face_bottom = int(184 * scale)
    face_radius = int(24 * scale)
    stroke = max(2, int(12 * scale))
    draw.rounded_rectangle(
        [face_left, face_top, face_right, face_bottom],
        radius=face_radius,
        outline=WHITE,
        width=stroke,
    )

    # 左眼
    draw.ellipse(
        [int(96 * scale), int(104 * scale), int(120 * scale), int(128 * scale)],
        fill=WHITE,
    )
    # 右眼
    draw.ellipse(
        [int(136 * scale), int(104 * scale), int(160 * scale), int(128 * scale)],
        fill=WHITE,
    )

    # 天线
    draw.line(
        [(int(128 * scale), int(72 * scale)), (int(128 * scale), int(48 * scale))],
        fill=WHITE,
        width=stroke,
    )
    draw.ellipse(
        [int(116 * scale), int(36 * scale), int(140 * scale), int(60 * scale)],
        fill=WHITE,
    )

    return img


def main() -> int:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "build")
    os.makedirs(out_dir, exist_ok=True)

    # 主图标：512x512 PNG
    main_icon = draw_icon(BASE_SIZE)
    main_icon.save(os.path.join(out_dir, "icon.png"))
    print(f"Saved {os.path.join(out_dir, 'icon.png')}")

    # 多尺寸 PNG（electron / Linux 启动器 / 任务栏）
    sizes = [16, 24, 32, 48, 64, 128, 256, 512]
    png_paths: list[tuple[int, str]] = []
    for s in sizes:
        path = os.path.join(out_dir, f"icon-{s}.png")
        draw_icon(s).save(path)
        png_paths.append((s, path))
        print(f"Saved {path}")

    # Windows .ico：包含多尺寸（系统任务栏必需）
    ico_path = os.path.join(out_dir, "icon.ico")
    base_for_ico = draw_icon(256)
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base_for_ico.save(
        ico_path,
        format="ICO",
        sizes=ico_sizes,
    )
    print(f"Saved {ico_path}")

    # macOS .icns（生成时如有 pillow-icns 插件会用到；这里仅在 macOS 上生成 .icns 文件夹占位）
    return 0


if __name__ == "__main__":
    sys.exit(main())
