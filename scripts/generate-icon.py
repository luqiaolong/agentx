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


# 机器人相对画布大小的缩放系数，按标题栏 Bot 图标占容器比例对齐。
# 标题栏：Bot 图标 20px / 容器 28px ≈ 71.4%。
# 任务栏机器人整体外接矩形也应约占图标短边的 71.4%，因之前过大缩小 20%，
# 最终 ROBOT_SCALE = 2.75 * 0.8 ≈ 2.2。
ROBOT_SCALE = 2.2


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    scale = size / BASE_SIZE
    radius = int(RADIUS_RATIO * size)

    # 圆角矩形背景
    draw.rounded_rectangle([0, 0, size, size], radius=radius, fill=BG)

    # 机器人脸：矩形脸 + 两个圆眼 + 单天线
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

    # 天线：从脸顶部向上延伸，长度缩短使整体白色区域接近方形
    antenna_stem_len = 16 * ROBOT_SCALE
    antenna_top_y = center - face_half_h - antenna_stem_len
    antenna_ball_y = center - face_half_h - antenna_stem_len
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
    # 使用 append_images 显式保存每一帧，避免 Pillow 的 sizes 参数只生成单帧。
    ico_path = os.path.join(out_dir, "icon.ico")
    ico_sizes = [256, 128, 64, 48, 32, 24, 16]
    ico_images = [draw_icon(s) for s in ico_sizes]
    ico_images[0].save(
        ico_path,
        format="ICO",
        append_images=ico_images[1:],
    )
    print(f"Saved {ico_path}")

    # macOS .icns（生成时如有 pillow-icns 插件会用到；这里仅在 macOS 上生成 .icns 文件夹占位）
    return 0


if __name__ == "__main__":
    sys.exit(main())
