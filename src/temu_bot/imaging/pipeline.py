"""图片产出管线：把表格里的原图变成平台能收的合规图。

三条硬约定：
1. **原图只读**——绝不覆盖、绝不原地改。所有产出落到 `work/<row_key>/out/`。
2. **已合规就直通**——尺寸本来就合格时做字节复制，不重新编码，避免二次损失。
3. **写完当场复验**——产出后再按规格量一遍，不合格就报出来，别等平台打回。
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from temu_bot.config import Config
from temu_bot.imaging.resolve import HOW_LABELS, FolderIndex, index_folder
from temu_bot.imaging.specs import SPECS, ImageInfo, ImageSpec, inspect
from temu_bot.sheet.schema import SheetRow

log = logging.getLogger(__name__)

# 哪一列对应哪个规格
SLOT_SPECS: dict[str, str] = {"main": "main", "material": "material"}

# 逐级降质，直到体积达标
QUALITY_LADDER: tuple[int, ...] = (95, 90, 85, 80, 75, 70, 65, 60, 55, 50)

PASS = "pass"        # 已合规，字节复制
COVER = "cover"      # 充满裁剪
CONTAIN = "contain"  # 完整放入 + 补边


@dataclass(slots=True)
class PlannedImage:
    slot: str
    spec: ImageSpec
    src: Path
    dst: Path
    action: str                     # pass / cover / contain
    before: ImageInfo | None = None
    after: ImageInfo | None = None
    note: str = ""

    @property
    def is_rewrite(self) -> bool:
        return self.action != PASS

    def line(self) -> str:
        head = f"{self.spec.label_with_grounding} {self.src.name}"
        if self.note:
            head += f"［{self.note}］"
        if self.action == PASS:
            return f"✅ {head} 已合规（{(self.before.size_label() if self.before else '?')}），原样复制"
        was = self.before.size_label() if self.before and self.before.exists else "?"
        tail = f"{was} → {self.spec.width}x{self.spec.height}"
        if self.after and self.after.exists:
            if self.after.ok:
                tail += f"，产出复验通过（{self.after.nbytes / 1024:.0f}KB）"
            else:
                tail += f"，⚠️ 产出仍不合格：{'；'.join(self.after.problems)}"
        return f"🔧 {head} {self.action} {tail}"


@dataclass(slots=True)
class PrepResult:
    row_key: str
    out_dir: Path
    items: list[PlannedImage] = field(default_factory=list)
    written: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        for item in self.items:
            # 原图缺失/读不出来 → 这条计划根本没法执行
            if item.before is None or not item.before.exists:
                return True
            # 落盘后复验不过 → 产出不可用
            if item.after is not None and not item.after.ok:
                return True
        return False

    @property
    def rewrites(self) -> int:
        return sum(1 for i in self.items if i.is_rewrite)


def out_dir_for(cfg: Config, row_key: str) -> Path:
    return cfg.work_dir / row_key / "out"


def plan_row(
    cfg: Config,
    row: SheetRow,
    *,
    images_dir: Path | None = None,
    index: FolderIndex | None = None,
) -> list[PlannedImage]:
    """只读地算出"每张图该怎么处理"（不写任何文件）。

    图片不是靠 `文件夹/名字` 死拼的——表里的名字是手填的，实际文件名常被改过，
    所以走 `resolve` 去文件夹里**找**（见 imaging/resolve.py）。
    """
    out_dir = out_dir_for(cfg, row.row_key)
    folder = Path(images_dir) if images_dir else (Path(row.folder) if row.folder else None)
    if folder is None:
        return []

    if index is None:
        index = index_folder(folder)

    planned: list[PlannedImage] = []

    for slot, spec_key in SLOT_SPECS.items():
        spec = SPECS[spec_key]
        for name in row.images.get(slot, []):
            found = index.resolve(name)
            src = found.path or (folder / name)   # 没找到时留个"本该是哪个路径"给报错用
            info = inspect(src, spec) if src.exists() else None
            if info is not None and info.ok:
                action = PASS
            elif spec.fit == "cover":
                action = COVER
            else:
                action = CONTAIN
            note = "" if found.how == "exact" else HOW_LABELS.get(found.how, found.how)
            planned.append(
                PlannedImage(
                    slot=slot,
                    spec=spec,
                    src=src,
                    dst=out_dir / f"{slot}-{src.name}",
                    action=action,
                    before=info,
                    note=note,
                )
            )
    return planned


def save_under_limit(image: Image.Image, dst: Path, spec: ImageSpec) -> None:
    """按质量阶梯保存，直到体积达标；JPEG 无透明通道，先铺白底。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for quality in QUALITY_LADDER:
        image.save(dst, format="JPEG", quality=quality, optimize=True, progressive=True)
        if dst.stat().st_size <= spec.max_bytes:
            return
    # 阶梯走完还超：上面每一级都已写盘，最后一个是体积最小的，留着并让复验去报
    log.warning("图片压到 q=%d 仍超过 %d 字节：%s", QUALITY_LADDER[-1], spec.max_bytes, dst)


def _render(src: Path, dst: Path, spec: ImageSpec) -> None:
    """按规格重出图。cover=充满裁剪（不留边）；contain=完整放入（补背景边）。"""
    with Image.open(src) as opened:
        image = opened.convert("RGB")
        if spec.fit == "cover":
            image = ImageOps.fit(
                image,
                (spec.width, spec.height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        else:
            image.thumbnail((spec.width, spec.height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (spec.width, spec.height), spec.background)
            canvas.paste(
                image,
                ((spec.width - image.width) // 2, (spec.height - image.height) // 2),
            )
            image = canvas
        save_under_limit(image, dst, spec)


def process_row(
    cfg: Config,
    row: SheetRow,
    *,
    dry_run: bool = True,
    images_dir: Path | None = None,
    index: FolderIndex | None = None,
) -> PrepResult:
    """干跑只出计划；dry_run=False 时落盘到 `work/<row_key>/out/` 并当场复验。"""
    out_dir = out_dir_for(cfg, row.row_key)
    result = PrepResult(row_key=row.row_key, out_dir=out_dir)
    result.items = plan_row(cfg, row, images_dir=images_dir, index=index)

    missing = [i for i in result.items if i.before is None or not i.before.exists]
    if missing:
        return result  # 缺原图就不产出，交给上游报错

    if dry_run:
        result.written = tuple(str(i.dst) for i in result.items)
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for item in result.items:
        if item.action == PASS:
            shutil.copyfile(item.src, item.dst)   # 字节复制，不重编码
        else:
            _render(item.src, item.dst, item.spec)
        item.after = inspect(item.dst, item.spec)
        written.append(str(item.dst))

    result.written = tuple(written)
    _write_manifest(result)
    return result


def _write_manifest(result: PrepResult) -> None:
    """产出清单落到 out 目录，便于回头查"这批图是怎么来的"。"""
    payload = {
        "row_key": result.row_key,
        "out_dir": str(result.out_dir),
        "rewrites": result.rewrites,
        "items": [
            {
                "slot": i.slot,
                "spec": i.spec.key,
                "target": f"{i.spec.width}x{i.spec.height}",
                "action": i.action,
                "src": str(i.src),
                "dst": str(i.dst),
                "before": (
                    {
                        "size": i.before.size_label(),
                        "bytes": i.before.nbytes,
                        "problems": list(i.before.problems),
                    }
                    if i.before
                    else None
                ),
                "after": (
                    {
                        "size": i.after.size_label(),
                        "bytes": i.after.nbytes,
                        "problems": list(i.after.problems),
                    }
                    if i.after
                    else None
                ),
            }
            for i in result.items
        ],
    }
    (result.out_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
