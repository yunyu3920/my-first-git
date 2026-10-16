"""图片格式转换（对应桌面工具 `Core.ps1` 的 Invoke-ImageConvert + `convert.mjs`）。

桌面版靠 Node + sharp；这里用 **Pillow 直接做**（venv 里已装 12.3，`
features.check('avif')` 和 `webp` 都是 True，AVIF 能读也能写），
不再需要 Node。

行为照抄：

- 目标格式 png / jpg / webp / avif；**质量默认 100**（会夹到 1..100）。
  质量只对 jpeg / webp / avif 生效；**png 是无损的**，桌面版给 png 传质量时其实
  只换成 `compressionLevel: 9`，这里对应 `optimize=True, compress_level=9`。
- **输出目录留空 = 跟源文件同目录**。
- **同批撞名加 `_1`、`_2`**：只按**本批已经产出的名字**判（大小写不敏感），
  **不看磁盘上原本有什么** —— 跟桌面版一致。
- 两种跳过（都算"跳过"，不算错）：
  - 目标路径和源路径是同一个（注意是按**路径相等**判的，不是"已经是这个格式"——
    `.png→png` 但输出到别的目录，照样会真转一次）
  - 目标已存在且没勾"覆盖"
- **jpeg 要把透明铺白底**（sharp 的 `flatten({background:"#ffffff"})`）。
- **默认不删源文件**；这轮也不提供删除功能。

与桌面版的已知差异（界面上会写明）：

- **HEIC 读不了**：Pillow 没有注册 `.heic`/`.heif` 解码器，这类文件会被扫到但
  转换时报"打不开"。
- **动图只能尽力而为**：sharp 的 `animated:true` 有完整支持，Pillow 这边对
  webp/png 用 `save_all` 尽量保留多帧，不保证与 sharp 完全一致。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from temu_bot.toolbox.files import list_images
from temu_bot.toolbox.result import FAILED, OK, SKIPPED, WOULD, ToolItem, ToolReport

log = logging.getLogger(__name__)

# 目标格式 → 输出扩展名（jpeg 落到 .jpg，跟桌面版一致）
EXT_BY_FORMAT = {"jpeg": "jpg", "png": "png", "webp": "webp", "avif": "avif"}

# 界面上给的下拉项（顺序照桌面工具）
TARGET_FORMATS = ("png", "jpg", "webp", "avif")

# Pillow 存的时候用的编码器名
PILLOW_FORMAT = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF"}

PNG_COMPRESS_LEVEL = 9
HEIC_HINT = "Pillow 没有 HEIC 解码器（桌面工具箱靠 sharp 才读得了），这类文件换不了格式"


class ConvertError(RuntimeError):
    pass


@dataclass(slots=True)
class Probe:
    ok: bool
    fmt: str = ""
    width: int = 0
    height: int = 0
    frames: int = 1
    error: str = ""

    def label(self) -> str:
        if not self.ok:
            return self.error
        return f"{self.fmt or '?'} {self.width}x{self.height}"


def normalize_format(name: str | None) -> str:
    """`jpg`/`jpeg` → `jpeg`；`tif` → `tiff`；`heic` → `heif`（跟 convert.mjs 一致）。"""
    if not name:
        return ""
    got = str(name).lower().lstrip(".")
    if got in ("jpg", "jpeg"):
        return "jpeg"
    if got == "tif":
        return "tiff"
    if got == "heic":
        return "heif"
    return got


def probe_one(src: Path) -> Probe:
    """只读探测：格式 + 尺寸 + 帧数。坏文件不抛异常，包成 error。"""
    try:
        with Image.open(src) as im:
            return Probe(
                ok=True,
                fmt=(im.format or src.suffix.lstrip(".")).lower(),
                width=im.width,
                height=im.height,
                frames=getattr(im, "n_frames", 1),
            )
    except UnidentifiedImageError as exc:
        hint = HEIC_HINT if src.suffix.lower() in (".heic", ".heif") else ""
        return Probe(ok=False, error=f"打不开：{exc}{('；' + hint) if hint else ''}")
    except (OSError, ValueError) as exc:
        return Probe(ok=False, error=f"打不开：{exc}")


def natural_target(src: Path, target_format: str, output_dir: Path | None) -> Path:
    """不撞名时本来会写到的那个路径（还没加 `_1` 后缀）。"""
    ext = EXT_BY_FORMAT.get(target_format, target_format)
    directory = Path(output_dir) if output_dir else src.parent
    return directory / f"{src.stem}.{ext}"


def output_path_for(
    src: Path, target_format: str, output_dir: Path | None, taken: set[str]
) -> Path:
    """本批撞名就加 `_1`、`_2`…（`taken` 存的是小写路径）。"""
    candidate = natural_target(src, target_format, output_dir)
    ext = EXT_BY_FORMAT.get(target_format, target_format)
    n = 1
    while str(candidate).lower() in taken:
        candidate = candidate.parent / f"{src.stem}_{n}.{ext}"
        n += 1
    return candidate


def _flatten_to_rgb(im: Image.Image) -> Image.Image:
    """jpeg 没有透明通道——先把透明铺成白底，别让透明变成黑块。"""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return im.convert("RGB")


def _save(im_path: Path, dst: Path, target_format: str, quality: int | None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(im_path) as opened:
        opened.load()
        frames = getattr(opened, "n_frames", 1)
        animated: list[Image.Image] = []
        if frames > 1:
            for index in range(1, frames):
                opened.seek(index)
                animated.append(opened.copy())
            opened.seek(0)

        if target_format == "jpeg":
            image = _flatten_to_rgb(opened)
            kwargs: dict[str, object] = {}
            if quality is not None:
                kwargs["quality"] = quality
            image.save(dst, format="JPEG", **kwargs)
            return

        if target_format == "png":
            # 无损；桌面版给 png 的"质量"其实只表示开到最大压缩
            image = opened
            if image.mode == "P":
                image = image.convert("RGBA")
            image.save(dst, format="PNG", optimize=True, compress_level=PNG_COMPRESS_LEVEL)
            return

        kwargs = {}
        if quality is not None:
            kwargs["quality"] = quality
        if animated:
            kwargs["save_all"] = True
            kwargs["append_images"] = animated
        opened.save(dst, format=PILLOW_FORMAT.get(target_format, target_format.upper()), **kwargs)


def _convert_core(
    folder: Path,
    *,
    target_format: str = "png",
    quality: int = 100,
    output_dir: Path | None = None,
    recursive: bool = False,
    overwrite: bool = False,
    dry_run: bool = True,
) -> ToolReport:
    target = normalize_format(target_format)
    if target not in EXT_BY_FORMAT:
        raise ConvertError(
            f"不支持转成「{target_format}」（可选 {'/'.join(TARGET_FORMATS)}）"
        )
    quality_value = max(1, min(100, int(quality))) if quality else None

    files = list_images(folder, recursive=recursive)
    items: list[ToolItem] = []
    outputs: list[str] = []
    taken: set[str] = set()

    for src in files:
        # 先按"不撞名时会写到的那个路径"判同路径。
        # 判的必须是**没加 _1 后缀**的那个：同一个目录里既转又扫时，
        # 刚才转出来的 a.webp 会被当成新源再转一次变成 a_1.webp，越跑越多。
        # 桌面工具的同名规则本意就是拦这个（"目标与源文件相同"），
        # 只是它是拿加过后缀的名字去比的，撞上就漏了。
        natural = natural_target(src, target, output_dir)
        if natural.resolve() == src.resolve():
            taken.add(str(natural).lower())  # 占住这个名字，别让后面的文件写它
            items.append(ToolItem(src.name, f"转 {target}", str(natural), SKIPPED, "目标与源文件相同"))
            continue

        dst = output_path_for(src, target, output_dir, taken)
        taken.add(str(dst).lower())
        if not overwrite and dst.exists():
            items.append(
                ToolItem(src.name, f"转 {target}", str(dst), SKIPPED, "目标已存在（未勾选覆盖）")
            )
            continue

        info = probe_one(src)
        if not info.ok:
            items.append(ToolItem(src.name, f"转 {target}", str(dst), FAILED, info.error))
            continue

        if dry_run:
            detail = info.label()
            if info.frames > 1:
                detail += f"（{info.frames} 帧，动图只尽力保留）"
            items.append(ToolItem(src.name, f"转 {target}", str(dst), WOULD, detail))
            continue

        try:
            _save(src, dst, target, quality_value)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            items.append(ToolItem(src.name, f"转 {target}", str(dst), FAILED, f"转换失败：{exc}"))
            continue

        after = probe_one(dst)
        detail = after.label() if after.ok else ""
        if info.frames > 1:
            detail += f"（源 {info.frames} 帧，动图只尽力保留）"
        items.append(ToolItem(src.name, f"转 {target}", str(dst), OK, detail))
        outputs.append(str(dst))

    note = f"转成 {target}"
    if output_dir:
        note += f"，输出到 {output_dir}"
    if not files:
        note = f"这个文件夹里没有图片：{folder}"
    return ToolReport(items=items, outputs=outputs, note=note)


def plan(
    folder: Path,
    *,
    target_format: str = "png",
    quality: int = 100,
    output_dir: Path | None = None,
    recursive: bool = False,
    overwrite: bool = False,
) -> ToolReport:
    """干跑预览：**只读探测**每个文件的格式与尺寸，一个文件都不写。"""
    try:
        return _convert_core(
            folder,
            target_format=target_format,
            quality=quality,
            output_dir=output_dir,
            recursive=recursive,
            overwrite=overwrite,
            dry_run=True,
        )
    except (NotADirectoryError, ConvertError) as exc:
        return ToolReport(items=[ToolItem(str(folder), "转换", "", FAILED, str(exc))])


def run(
    folder: Path,
    *,
    target_format: str = "png",
    quality: int = 100,
    output_dir: Path | None = None,
    recursive: bool = False,
    overwrite: bool = False,
) -> ToolReport:
    """真转。原图**只读**，输出写到输出目录（默认跟源文件同目录）。"""
    try:
        return _convert_core(
            folder,
            target_format=target_format,
            quality=quality,
            output_dir=output_dir,
            recursive=recursive,
            overwrite=overwrite,
            dry_run=False,
        )
    except (NotADirectoryError, ConvertError, OSError) as exc:
        log.exception("格式转换失败")
        return ToolReport(items=[ToolItem(str(folder), "转换", "", FAILED, str(exc))])
