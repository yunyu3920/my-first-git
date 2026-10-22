"""尺寸与格式：把一批图片统一改成指定宽高 + 常见格式。

这是「图片批处理生成清单」页面第二步（用户 2026-10-18 要求）：

- 默认 **1340 × 1785 / JPG**；页面另给 800×800（素材图）一个常用规格；
- 缩放方式三选一：**裁剪填满（cover，默认）** / 完整保留留白（contain） / 拉伸（stretch）；
- **默认直接覆盖原图、保持文件名**（stem 不变，扩展名统一成目标格式）：
  `裙子.jpg` → 原地变成 1340×1785 的 `裙子.jpg`；`裙子.png` → `裙子.jpg`（原 png 删掉）。
  这样下一步「11 位 ID - 导表 - 保留 11 位 ID」扫这个文件夹时，
  一个名字只会有一张处理好的图，不会把原图和处理结果都算进去；
- 覆盖是**先写 .tmp → 回读校验尺寸 → os.replace 原子替换**，失败不会写坏原图；
- `inplace=False` 时退回旧行为：另存为 `<原名>_<宽>x<高>.<扩展名>`，原图只读；
- 和 `convert` 一样：默认干跑（一个字节都不写），真跑要显式 live；
- 动图只处理第一帧；HEIC 仍然读不了（Pillow 没解码器）。

为什么默认 cover：商品图要铺满 Temu 的主图框，留白会让主体显小；
1340×1785 就是 Temu 主图规格，和常见 3:4 图只差一点点，裁剪损失很小。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps, UnidentifiedImageError

from temu_bot.toolbox.convert import HEIC_HINT, _flatten_to_rgb, normalize_format, probe_one
from temu_bot.toolbox.files import list_images
from temu_bot.toolbox.result import FAILED, OK, SKIPPED, WOULD, ToolItem, ToolReport

log = logging.getLogger(__name__)

# 用户定的默认值（2026-10-18 晚改成 1340×1785 / JPG，并默认覆盖原图）
DEFAULT_WIDTH = 1340
DEFAULT_HEIGHT = 1785
DEFAULT_FORMAT = "jpg"
DEFAULT_QUALITY = 95

# 页面上的「常用规格」下拉用这几个（值 = 宽 x 高 / 格式）
PRESETS: tuple[tuple[int, int, str], ...] = (
    (1340, 1785, "jpg"),
    (800, 800, "jpg"),
)

# 内部格式名（normalize_format 会把 jpg 变成 jpeg）
TARGET_FORMATS = ("jpg", "png", "webp")
_EXT = {"jpeg": "jpg", "png": "png", "webp": "webp"}
_PILLOW = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}

FIT_MODES = ("cover", "contain", "stretch")
FIT_LABELS = {
    "cover": "裁剪填满",
    "contain": "完整保留（留白）",
    "stretch": "拉伸",
}

MIN_SIDE = 16
MAX_SIDE = 10000


class ResizeError(RuntimeError):
    pass


def _target_format(name: str | None) -> str:
    """页面/CLI 传进来的格式 → 内部格式名。只接受 jpg/png/webp。"""
    got = normalize_format(name or DEFAULT_FORMAT)
    if got not in _EXT:
        raise ResizeError(f"不支持输出「{name}」（可选 {' / '.join(TARGET_FORMATS)}）")
    return got


def _check_size(width: int, height: int) -> tuple[int, int]:
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError) as exc:
        raise ResizeError(f"宽高必须是整数：{width} x {height}") from exc
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
        raise ResizeError(
            f"宽高必须在 {MIN_SIDE}~{MAX_SIDE} 之间（收到 {w}x{h}）"
        )
    return w, h


def _has_alpha(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)


def resize_image(
    im: Image.Image,
    width: int,
    height: int,
    *,
    fit: str = "cover",
) -> Image.Image:
    """把一帧图片变成**正好** width×height。"""
    if fit not in FIT_MODES:
        raise ResizeError(f"未知缩放方式「{fit}」（可选 {' / '.join(FIT_MODES)}）")

    # 手机拍的图常带 EXIF 旋转；先摆正，否则会横着裁
    im = ImageOps.exif_transpose(im)

    if fit == "stretch":
        return im.resize((width, height), Image.LANCZOS)

    if fit == "contain":
        copy = im.copy()
        copy.thumbnail((width, height), Image.LANCZOS)
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        if _has_alpha(copy):
            rgba = copy.convert("RGBA")
            canvas.paste(
                rgba,
                ((width - rgba.width) // 2, (height - rgba.height) // 2),
                rgba,
            )
        else:
            rgb = copy.convert("RGB")
            canvas.paste(rgb, ((width - rgb.width) // 2, (height - rgb.height) // 2))
        return canvas

    # cover：先按"能铺满"的比例放大，再从中间裁掉多余部分
    scale = max(width / im.width, height / im.height)
    new_w = max(width, round(im.width * scale))
    new_h = max(height, round(im.height * scale))
    scaled = im.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return scaled.crop((left, top, left + width, top + height))


def _save_resized(
    src: Path,
    dst: Path,
    *,
    width: int,
    height: int,
    target_format: str,
    fit: str,
    quality: int,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as opened:
        opened.load()
        if getattr(opened, "n_frames", 1) > 1:
            opened.seek(0)          # 动图只取第一帧
        image = resize_image(opened, width, height, fit=fit)

    if target_format == "jpeg":
        image = _flatten_to_rgb(image)      # 透明铺白底
        image.save(dst, format="JPEG", quality=quality, optimize=True)
        return

    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if _has_alpha(image) else "RGB")

    if target_format == "png":
        image.save(dst, format="PNG", optimize=True, compress_level=9)
        return

    image.save(dst, format="WEBP", quality=quality, method=6)


def _save_inplace(src: Path, opts: ResizeOptions) -> tuple[Path, str]:
    """覆盖原图：保持文件名（stem 不变），扩展名统一成目标格式。

    安全顺序：**先写同目录的 .tmp → 回读校验尺寸 → os.replace 原子替换**。
    中途失败只会留下一个临时文件（会删掉），原图不会被写坏。

    如果原文件不是目标扩展名（如 `.png`），替换成功后会**删掉原文件**，
    保证同一个名字只留一份，下一步「11 位 ID」不会把原图和处理结果都扫进去。
    """
    dst = src.parent / f"{src.stem}.{_EXT[opts.target_format]}"
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        _save_resized(
            src,
            tmp,
            width=opts.width,
            height=opts.height,
            target_format=opts.target_format,
            fit=opts.fit,
            quality=opts.quality,
        )
        with Image.open(tmp) as im:
            im.load()
            if (im.width, im.height) != (opts.width, opts.height):
                raise ResizeError(
                    f"生成结果尺寸不对：{im.width}x{im.height}，期望 {opts.width}x{opts.height}"
                )
        os.replace(tmp, dst)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    same_file = str(src).lower() == str(dst).lower()
    if not same_file:
        try:
            src.unlink()
        except OSError as exc:
            raise ResizeError(
                f"新图已写出（{dst.name}），但删不掉原文件（{src.name}）：{exc}"
            ) from exc
    return dst, ("已覆盖原图" if same_file else f"已转成 {dst.name}，原文件已删除")


@dataclass(slots=True)
class ResizeOptions:
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    target_format: str = DEFAULT_FORMAT
    fit: str = "cover"
    quality: int = DEFAULT_QUALITY
    output_dir: Path | None = None
    recursive: bool = False
    overwrite: bool = False
    # True = **直接覆盖原图、保持文件名**（扩展名统一成目标格式，默认）
    # False = 另存副本，名字加 `_宽x高`
    inplace: bool = True

    def normalized(self) -> "ResizeOptions":
        w, h = _check_size(self.width, self.height)
        fmt = _target_format(self.target_format)
        fit = self.fit or "cover"
        if fit not in FIT_MODES:
            raise ResizeError(f"未知缩放方式「{fit}」（可选 {' / '.join(FIT_MODES)}）")
        quality = max(1, min(100, int(self.quality or DEFAULT_QUALITY)))
        return ResizeOptions(
            width=w,
            height=h,
            target_format=fmt,
            fit=fit,
            quality=quality,
            output_dir=Path(self.output_dir) if self.output_dir else None,
            recursive=bool(self.recursive),
            overwrite=bool(self.overwrite),
            inplace=bool(self.inplace),
        )


def _base_name(src: Path, width: int, height: int, target_format: str) -> str:
    ext = _EXT[target_format]
    return f"{src.stem}_{width}x{height}.{ext}"


def output_path_for(
    src: Path,
    opts: ResizeOptions,
    taken: set[str],
) -> Path:
    """结果路径：`<原名>_<宽>x<高>.<ext>`；本批撞名再加 `_1`、`_2`。"""
    directory = opts.output_dir or src.parent
    base = _base_name(src, opts.width, opts.height, opts.target_format)
    candidate = directory / base
    stem = Path(base).stem
    ext = Path(base).suffix
    n = 1
    while str(candidate).lower() in taken:
        candidate = directory / f"{stem}_{n}{ext}"
        n += 1
    return candidate


def _resize_core(
    folder: Path,
    opts: ResizeOptions,
    *,
    dry_run: bool,
    on_progress: Callable[[int, int, Path], None] | None = None,
    probe: bool = True,
) -> ToolReport:
    opts = opts.normalized()
    files = list_images(folder, recursive=opts.recursive)
    items: list[ToolItem] = []
    outputs: list[str] = []
    taken: set[str] = set()
    action = f"{opts.width}x{opts.height} {_EXT[opts.target_format].upper()}"

    for index, src in enumerate(files, 1):
        if on_progress is not None:
            try:
                on_progress(index, len(files), src)
            except Exception:  # noqa: BLE001 - 进度回调是旁路，不能带崩处理
                log.warning("进度回调出错（忽略）", exc_info=True)
        if opts.inplace:
            # 覆盖模式：**保持原名（stem）**，扩展名统一成目标格式
            dst = src.parent / f"{src.stem}.{_EXT[opts.target_format]}"
        else:
            dst = output_path_for(src, opts, taken)
            taken.add(str(dst).lower())
            if not opts.overwrite and dst.exists():
                items.append(
                    ToolItem(src.name, action, str(dst), SKIPPED, "目标已存在（未勾选覆盖）")
                )
                continue

        info = probe_one(src) if probe else None
        if info is not None and not info.ok:
            items.append(ToolItem(src.name, action, str(dst), FAILED, info.error))
            continue

        if dry_run:
            if info is not None:
                detail = (
                    f"{info.fmt} {info.width}x{info.height} → "
                    f"{_EXT[opts.target_format]} {opts.width}x{opts.height}"
                    f"（{FIT_LABELS[opts.fit]}）"
                )
            else:
                # 大文件夹预览：不逐张打开图片，先把"会怎么处理"列出来（快得多）
                detail = (
                    f"→ {_EXT[opts.target_format]} {opts.width}x{opts.height}"
                    f"（{FIT_LABELS[opts.fit]}）"
                )
            if opts.inplace:
                same = str(src).lower() == str(dst).lower()
                detail += (
                    "；原图将被覆盖"
                    if same
                    else f"；将转成同名 {_EXT[opts.target_format]} 并删掉原文件"
                )
            if info is not None and info.frames > 1:
                detail += f"；{info.frames} 帧，只取第一帧"
            items.append(ToolItem(src.name, action, str(dst), WOULD, detail))
            continue

        try:
            if opts.inplace:
                dst, note = _save_inplace(src, opts)
            else:
                _save_resized(
                    src,
                    dst,
                    width=opts.width,
                    height=opts.height,
                    target_format=opts.target_format,
                    fit=opts.fit,
                    quality=opts.quality,
                )
                note = ""
        except (UnidentifiedImageError, OSError, ValueError, ResizeError) as exc:
            items.append(ToolItem(src.name, action, str(dst), FAILED, f"处理失败：{exc}"))
            continue

        after = probe_one(dst)
        detail = (
            f"{after.fmt} {after.width}x{after.height}（{FIT_LABELS[opts.fit]}）"
            if after.ok
            else ""
        )
        if opts.inplace:
            detail = f"{detail}；{note}" if detail else note
        if info is not None and info.frames > 1:
            detail += f"；源 {info.frames} 帧，只取第一帧"
        items.append(ToolItem(src.name, action, str(dst), OK, detail))
        outputs.append(str(dst))

    note = (
        f"尺寸与格式：{opts.width}×{opts.height} · "
        f"{_EXT[opts.target_format].upper()}（{FIT_LABELS[opts.fit]}）"
    )
    if opts.inplace:
        note += "；直接覆盖原图（文件名不变）"
    elif opts.output_dir:
        note += f"，输出到 {opts.output_dir}"
    if not files:
        note = f"这个文件夹里没有图片：{folder}"
    return ToolReport(items=items, outputs=outputs, note=note)


def plan(
    folder: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    target_format: str = DEFAULT_FORMAT,
    fit: str = "cover",
    quality: int = DEFAULT_QUALITY,
    output_dir: Path | None = None,
    recursive: bool = False,
    overwrite: bool = False,
    inplace: bool = True,
    on_progress: Callable[[int, int, Path], None] | None = None,
    probe: bool = True,
) -> ToolReport:
    """干跑预览：只读探测，一个字节都不写。

    `probe=False` 时不逐张打开图片（只列目标名），大文件夹预览会快很多；
    代价是预览里看不到源图尺寸，坏图要到执行时才会报。
    """
    opts = ResizeOptions(
        width=width,
        height=height,
        target_format=target_format,
        fit=fit,
        quality=quality,
        output_dir=output_dir,
        recursive=recursive,
        overwrite=overwrite,
        inplace=inplace,
    )
    try:
        return _resize_core(
            folder, opts, dry_run=True, on_progress=on_progress, probe=probe
        )
    except (NotADirectoryError, ResizeError) as exc:
        return ToolReport(items=[ToolItem(str(folder), "尺寸与格式", "", FAILED, str(exc))])


def run(
    folder: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    target_format: str = DEFAULT_FORMAT,
    fit: str = "cover",
    quality: int = DEFAULT_QUALITY,
    output_dir: Path | None = None,
    recursive: bool = False,
    overwrite: bool = False,
    inplace: bool = True,
    on_progress: Callable[[int, int, Path], None] | None = None,
) -> ToolReport:
    """真处理。**默认覆盖原图、保持文件名**；`inplace=False` 才另存副本。"""
    opts = ResizeOptions(
        width=width,
        height=height,
        target_format=target_format,
        fit=fit,
        quality=quality,
        output_dir=output_dir,
        recursive=recursive,
        overwrite=overwrite,
        inplace=inplace,
    )
    try:
        return _resize_core(folder, opts, dry_run=False, on_progress=on_progress)
    except (NotADirectoryError, ResizeError, OSError) as exc:
        log.exception("尺寸与格式处理失败")
        return ToolReport(items=[ToolItem(str(folder), "尺寸与格式", "", FAILED, str(exc))])
