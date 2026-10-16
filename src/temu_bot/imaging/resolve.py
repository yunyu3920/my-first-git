"""按表格里写的图片名，去图片文件夹里**找**出真实的文件。

为什么不能只做 `文件夹 / 名字` 的拼接：
表格是人手填的，文件名却常被改过——扩展名不同（.jpg/.jpeg/.png）、
大小写不同、被放进子文件夹、前后多了几个字。直接拼路径会大面积"找不到"，
而其实图就在那儿。

策略按可靠度从高到低依次尝试，**只要不唯一就不猜**：
    exact     完全一致
    casefold  忽略大小写
    stem      忽略扩展名（x.jpg ↔ x.png）
    recurse   在子文件夹里按文件名找
    prefix    唯一的前缀匹配（表里写 123.jpg，文件夹里是 123-素材图.jpg）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif")

EXACT = "exact"
CASEFOLD = "casefold"
STEM = "stem"
RECURSE = "recurse"
PREFIX = "prefix"
MISSING = "missing"
AMBIGUOUS = "ambiguous"

HOW_LABELS = {
    EXACT: "文件名一致",
    CASEFOLD: "忽略大小写",
    STEM: "扩展名不同",
    RECURSE: "在子文件夹里",
    PREFIX: "唯一前缀匹配",
    MISSING: "找不到",
    AMBIGUOUS: "匹配到多张，不敢猜",
}


@dataclass(frozen=True, slots=True)
class Resolved:
    name: str                       # 表格里写的名字
    path: Path | None               # 找到的真实文件；没找到/有歧义时为 None
    how: str = MISSING
    candidates: tuple[Path, ...] = ()

    @property
    def ok(self) -> bool:
        return self.path is not None

    def describe(self) -> str:
        if self.ok:
            rel = self.path.name
            extra = f"（{HOW_LABELS[self.how]}）" if self.how != EXACT else ""
            return f"✅ {self.name} → {rel}{extra}"
        if self.how == AMBIGUOUS:
            names = "、".join(p.name for p in self.candidates[:4])
            return f"❓ {self.name} → 匹配到多张（{names}），请把表格里的名字写准"
        return f"❌ {self.name} → 图片文件夹里找不到"


@dataclass(slots=True)
class FolderIndex:
    """一个图片文件夹的索引。批量解析时建一次就够，别每行都扫盘。"""

    folder: Path
    exists: bool = False
    recursive: bool = True
    files: tuple[Path, ...] = ()
    _exact_by_name: dict[str, Path] = field(default_factory=dict, repr=False)
    _by_name: dict[str, Path] = field(default_factory=dict, repr=False)
    _by_stem: dict[str, list[Path]] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, folder: Path, *, recursive: bool = True) -> FolderIndex:
        """建立索引。

        `recursive=False` 只看这一层——**快得多**，而且更符合"收集所选文件夹的图片"的语义：
        选到父目录（比如整个桌面）时，递归会把整棵子树走一遍（实测 1 秒起），
        既不快、也容易把不相干的图收进来。
        """
        folder = Path(folder)
        index = cls(folder=folder)
        if not folder.is_dir():
            return index
        index.exists = True
        index.recursive = recursive

        files: list[Path] = []
        walker = folder.rglob("*") if recursive else folder.glob("*")
        for path in walker:
            try:
                if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                    files.append(path)
            except OSError:
                continue
        files.sort(key=lambda p: (len(p.parts), p.name))
        index.files = tuple(files)

        for path in files:
            if path.parent == folder:
                index._exact_by_name.setdefault(path.name, path)
            index._by_name.setdefault(path.name.casefold(), path)
        for path in files:
            index._by_stem.setdefault(path.stem.casefold(), []).append(path)
        return index

    # ---------------------------------------------------------- 各策略
    def _exact(self, name: str) -> Path | None:
        """大小写**也要**一致。

        不能写成 `(folder / name).is_file()`——Windows 文件系统本身不区分大小写，
        那样 `abc.JPG` 会被当成精确命中，换到 Linux 上行为又不一样。
        所以一律拿索引进比对，跟操作系统无关。
        """
        return self._exact_by_name.get(name)

    def _casefold(self, name: str) -> Path | None:
        return self._by_name.get(name.casefold())

    def _stem(self, name: str) -> Resolved | None:
        hits = self._by_stem.get(Path(name).stem.casefold(), [])
        if len(hits) == 1:
            return Resolved(name=name, path=hits[0], how=STEM)
        if len(hits) > 1:
            return Resolved(name=name, path=None, how=AMBIGUOUS, candidates=tuple(hits))
        return None

    def _recurse(self, name: str) -> Path | None:
        """同名文件在子文件夹里（_by_name 已经覆盖递归结果，这里排除根目录同名）。"""
        hit = self._by_name.get(name.casefold())
        if hit is not None and hit.parent != self.folder:
            return hit
        return None

    def _prefix(self, name: str) -> Resolved | None:
        stem = Path(name).stem.casefold()
        if not stem:
            return None
        hits = [
            p
            for p in self.files
            if p.stem.casefold().startswith(stem) or stem.startswith(p.stem.casefold())
        ]
        if len(hits) == 1:
            return Resolved(name=name, path=hits[0], how=PREFIX)
        if len(hits) > 1:
            return Resolved(name=name, path=None, how=AMBIGUOUS, candidates=tuple(hits))
        return None

    # ---------------------------------------------------------- 对外
    def find_by_stem(self, stem: str, *, prefer_suffix: str | None = None) -> Resolved:
        """**只按"去扩展名后名字完全一致"找**，不走前缀猜测。

        为什么要单独的入口：`resolve()` 的 prefix 策略是"能唯一命中就认"，
        于是用 `123-素材图` 去找素材图时，会把主图 `123.jpg` 也命中
        （`"123-素材图".startswith("123")`），结果"缺素材图"的行被误判成找到了，
        素材图字段还被填成了主图本身。配对这类**语义明确**的查找，不能用模糊策略。
        """
        hits = list(self._by_stem.get(Path(stem).stem.casefold(), []))
        if not hits:
            return Resolved(name=stem, path=None, how=MISSING)
        if len(hits) > 1 and prefer_suffix:
            same = [p for p in hits if p.suffix.lower() == prefer_suffix.lower()]
            if len(same) == 1:
                return Resolved(name=stem, path=same[0], how=STEM)
        if len(hits) == 1:
            return Resolved(name=stem, path=hits[0], how=STEM)
        return Resolved(name=stem, path=None, how=AMBIGUOUS, candidates=tuple(hits))

    def resolve(self, name: str) -> Resolved:
        name = (name or "").strip()
        if not name:
            return Resolved(name=name, path=None, how=MISSING)
        if not self.exists:
            return Resolved(name=name, path=None, how=MISSING)

        hit = self._exact(name)
        if hit is not None:
            return Resolved(name=name, path=hit, how=EXACT)

        hit = self._casefold(name)
        if hit is not None:
            # 名字一致只是大小写不同 → casefold；只有路径更深才算 recurse
            how = RECURSE if hit.parent != self.folder else CASEFOLD
            return Resolved(name=name, path=hit, how=how)

        got = self._stem(name)
        if got is not None:
            return got

        got = self._prefix(name)
        if got is not None:
            return got

        return Resolved(name=name, path=None, how=MISSING)


def index_folder(folder: Path, *, recursive: bool = True) -> FolderIndex:
    return FolderIndex.build(folder, recursive=recursive)
