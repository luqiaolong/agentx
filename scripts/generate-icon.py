from PIL import Image, ImageDraw
import os
import sys

# 生成 AgentX 应用图标：圆角方块 + 机器人脸，与 UI 左上角风格保持一致。
# 注意：图标颜色固定为紫色 (#4f46e5)，不跟随主题色/品牌色变化，避免 Windows 任务栏
# 或标题栏图标在主题切换时被重新着色。
BG = (79, 70, 229)  # fixed AgentX purple (#4f46e5); do not theme
WHITE = (255, 255, 255)
RADIUS_RATIO = 48 / 256  # 圆角半径与尺寸的比例

BASE_SIZE = 512  # 最高分辨率
RADIUS = int(RADIUS_RATIO * BASE_SIZE)


# 机器人整体相对画布大小的缩放系数（1.0 = 原尺寸），用于和标题栏 Bot 图标视觉对齐
ROBOT_SCALE = 1.2


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    scale = size / BASE_SIZE
    radius = int(RADIUS_RATIO * size)

    # 圆角矩形背景
    draw.rounded_rectangle([0, 0, size, size], radius=radius, fill=BG)

    # 机器人脸：矩形脸 + 两个圆眼 + 天线
    # 所有元素以画布中心 (BASE_SIZE/2, BASE_SIZE/2) 为基准向外扩展，保持居中
    center = BASE_SIZE / 2
    face_half_w = 64 * ROBOT_SCALE
    face_half_h = 56 * ROBOT_SCALE
    face_left = int((center - face_half_w) * scale)
    face_top = int((center - face_half_h) * scale)
    face_right = int((center + face_half_w) * scale)
    face_bottom = int((center + face_half_h) * scale)
    face_radius = int(24 * ROBOT_SCALE * scale)
    stroke = max(2, int(12 * ROBOT_SCALE * scale))
    draw.rounded_rectangle(
        [face_left, face_top, face_right, face_bottom],
        radius=face_radius,
        outline=WHITE,
        width=stroke,
    )

    # 眼睛：相对脸中心偏移 (-20, -12) / (20, -12)，按 ROBOT_SCALE 放大
    eye_radius = 12 * ROBOT_SCALE
    eye_y_offset = 12 * ROBOT_SCALE
    eye_x_offset = 20 * ROBOT_SCALE
    left_eye_cx = center - eye_x_offset
    right_eye_cx = center + eye_x_offset
    eye_cy = center - eye_y_offset
    draw.ellipse(
        [
            int((left_eye_cx - eye_radius) * scale),
            int((eye_cy - eye_radius) * scale),
            int((left_eye_cx + eye_radius) * scale),
            int((eye_cy + eye_radius) * scale),
        ],
        fill=WHITE,
    )
    draw.ellipse(
        [
            int((right_eye_cx - eye_radius) * scale),
            int((eye_cy - eye_radius) * scale),
            int((right_eye_cx + eye_radius) * scale),
            int((eye_cy + eye_radius) * scale),
        ],
        fill=WHITE,
    )

    # 天线：从脸顶部向上延伸，按 ROBOT_SCALE 放大
    antenna_top_y = center - face_half_h - 24 * ROBOT_SCALE
    antenna_ball_y = center - face_half_h - 24 * ROBOT_SCALE
    antenna_base_y = center - face_half_h
    draw.line(
        [
            (int(center * scale), int(antenna_base_y * scale)),
            (int(center * scale), int(antenna_top_y * scale)),
        ],
        fill=WHITE,
        width=stroke,
    )
    antenna_radius = 12 * ROBOT_SCALE
    draw.ellipse(
        [
            int((center - antenna_radius) * scale),
            int((antenna_ball_y - antenna_radius) * scale),
            int((center + antenna_radius) * scale),
            int((antenna_ball_y + antenna_radius) * scale),
        ],
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
