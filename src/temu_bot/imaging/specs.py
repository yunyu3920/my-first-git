"""Temu 半托管图片规格 + 校验。

**主图/轮播图规格来自平台报错原文**（2026-10-15，商品 184807703165779045 的真实 errMsg）：

    服饰类目skc轮播图[…]校验失败，应符合宽高比例为3:4，
    宽>=1340px，高>=1785px，<=2M

所以目标是 **1340x1785**（既是 3:4 附近，又刚好卡住两个下限）。

**素材图 800x800 是从现有数据反推的**（表里 98 行的素材图都是 800x800，且平台从未就它报错），
标成 grounded=False，等拿到平台原文再收紧。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

MB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageSpec:
    key: str
    label: str
    width: int
    height: int
    fit: str                       # cover=充满裁剪（不留边） / contain=完整放入（补边）
    max_bytes: int
    background: tuple[int, int, int] = (255, 255, 255)
    ratio_tolerance: float = 0.01  # 1340x1785 = 0.7507，所以容差不能太紧
    grounded: bool = True          # 规格是否有平台原文依据

    @property
    def ratio(self) -> float:
        return self.width / self.height

    @property
    def label_with_grounding(self) -> str:
        return self.label if self.grounded else f"{self.label}(推定)"


MAIN_IMAGE = ImageSpec(
    key="main",
    label="主图/轮播图",
    width=1340,
    height=1785,
    fit="cover",
    max_bytes=2 * MB,
)

MATERIAL_IMAGE = ImageSpec(
    key="material",
    label="素材图",
    width=800,
    height=800,
    fit="contain",
    max_bytes=2 * MB,
    grounded=False,
)

SPECS: dict[str, ImageSpec] = {s.key: s for s in (MAIN_IMAGE, MATERIAL_IMAGE)}


@dataclass(slots=True)
class ImageInfo:
    path: Path
    exists: bool
    width: int = 0
    height: int = 0
    nbytes: int = 0
    fmt: str = ""
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exists and not self.problems

    def size_label(self) -> str:
        return f"{self.width}x{self.height}"

    def describe(self) -> str:
        if not self.exists:
            return "文件不存在"
        if self.ok:
            return f"{self.size_label()} {self.nbytes / 1024:.0f}KB"
        return f"{self.size_label()} {self.nbytes / 1024:.0f}KB ← {'；'.join(self.problems)}"


def problems_of(
    width: int,
    height: int,
    nbytes: int,
    spec: ImageSpec,
    *,
    ratio_tolerance: float | None = None,
) -> list[str]:
    """按规格挑毛病，返回人类可读的问题列表（空 = 合规）。"""
    tol = spec.ratio_tolerance if ratio_tolerance is None else ratio_tolerance
    problems: list[str] = []

    if not height or not width:
        return ["图片尺寸读不出来"]
    ratio = width / height
    if abs(ratio - spec.ratio) > tol:
        problems.append(f"比例 {ratio:.4f} 不符 3:4")
    if width < spec.width:
        problems.append(f"宽 {width} < {spec.width}")
    if height < spec.height:
        problems.append(f"高 {height} < {spec.height}")
    if nbytes > spec.max_bytes:
        problems.append(f"体积 {nbytes / MB:.2f}MB > {spec.max_bytes / MB:.0f}MB")
    return problems


def inspect(path: Path, spec: ImageSpec, *, ratio_tolerance: float | None = None) -> ImageInfo:
    """读一张图并按规格判定（只读）。"""
    path = Path(path)
    if not path.exists():
        return ImageInfo(path=path, exists=False, problems=("文件不存在",))

    nbytes = path.stat().st_size
    try:
        with Image.open(path) as im:
            width, height = im.size
            fmt = im.format or ""
    except Exception as exc:  # noqa: BLE001
        return ImageInfo(path=path, exists=True, nbytes=nbytes, problems=(f"打不开：{exc}",))

    return ImageInfo(
        path=path,
        exists=True,
        width=width,
        height=height,
        nbytes=nbytes,
        fmt=fmt,
        problems=tuple(problems_of(width, height, nbytes, spec, ratio_tolerance=ratio_tolerance)),
    )
