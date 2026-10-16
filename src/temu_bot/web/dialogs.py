"""系统文件对话框：由**后端**弹出，把选中的路径回给网页。

为什么必须由后端弹：浏览器出于安全，**不允许网页拿到本地的真实路径**
（`<input type="file">` 只给文件名，`showDirectoryPicker()` 也给不出绝对路径）。
但咱们的服务就跑在用户本机上，所以让 Python 去调 Windows 的文件对话框，
再把路径回给页面——这样用户点「选择文件夹」看到的就是熟悉的资源管理器。

用的是 shell32 的 `IFileDialog` + `FOS_PICKFOLDERS`，也就是**现代资源管理器样式**的
文件夹选择框（左侧导航栏 + 顶部路径栏 + 按钮写「选择文件夹」），
而不是 `SHBrowseForFolder` 那种很老的小树。

Unity 要点：
- PowerShell 脚本用 `-EncodedCommand`（base64/UTF-16LE）传，**彻底躲开引号转义的地狱**。
- 子进程有超时，用户把对话框晾着不管也不会把服务卡死。
- 结果用 `###PATH###` 前缀标记，避免把 PowerShell 的杂音当成路径。
"""

from __future__ import annotations

import base64
import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# 一次只允许弹一个对话框——同时弹两个，用户会疯
_DIALOG_LOCK = threading.Lock()

DEFAULT_TIMEOUT = 600          # 秒；用户可能开着对话框想事情
MARKER = "###PATH###"

# ---------------------------------------------------------------- PowerShell
# 说明：
# - IFileDialog 的 vtable 顺序**不能改**，用不到的方法也得占位，否则调错槽位。
# - FOS_* 常量见 Windows SDK（shobjidl.h）。
_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IFileDialog {
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
    void SetFileTypeIndex(uint iFileType);
    void GetFileTypeIndex(out uint piFileType);
    void Advise(IntPtr pfde, out uint pdwCookie);
    void Unadvise(uint dwCookie);
    void SetOptions(uint fos);
    void GetOptions(out uint pfos);
    void SetDefaultFolder(IShellItem psi);
    void SetFolder(IShellItem psi);
    void GetFolder(out IShellItem ppsi);
    void GetCurrentSelection(out IShellItem ppsi);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
    void GetResult(out IShellItem ppsi);
    void AddPlace(IShellItem psi, int fdap);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
    void Close(int hr);
    void SetClientGuid(ref Guid guid);
    void ClearClientData();
    void SetFilter(IntPtr pFilter);
}

[ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem ppsi);
    void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
    void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
    void Compare(IShellItem psi, uint hint, out int piOrder);
}

public static class Pick {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hWnd);

    public static string Show(string mode, string title, string initial, string filter) {
        var dlg = (IFileDialog)Activator.CreateInstance(Type.GetTypeFromCLSID(
            new Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")));

        uint FOS_PICKFOLDERS   = 0x00000020;
        uint FOS_FORCEFILESYSTEM = 0x00000040;
        uint FOS_PATHMUSTEXIST = 0x00000800;
        uint FOS_FILEMUSTEXIST = 0x00001000;
        uint FOS_OVERWRITEPROMPT = 0x00000002;
        uint FOS_NOVALIDATE = 0x00000100;

        uint opts = FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST;
        if (mode == "folder") { opts |= FOS_PICKFOLDERS; }
        else if (mode == "file") { opts |= FOS_FILEMUSTEXIST; }
        else { opts |= FOS_OVERWRITEPROMPT | FOS_NOVALIDATE; }

        if (title.Length > 0) { dlg.SetTitle(title); }
        dlg.SetOkButtonLabel(mode == "folder" ? "选择文件夹" : (mode == "save" ? "保存" : "打开"));
        dlg.SetOptions(opts);

        if (initial != null && initial.Length > 0) {
            try {
                if (mode == "folder") {
                    Guid iid = new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe");
                    object item;
                    SHCreateItemFromParsingName(initial, IntPtr.Zero, ref iid, out item);
                    if (item != null) { dlg.SetFolder((IShellItem)item); }
                } else {
                    dlg.SetFileName(initial);
                }
            } catch { /* 初始路径给错了不该让整个对话框失败 */ }
        }

        // SetFileTypes 需要 COMDLG_FILTERSPEC 数组，这里只在需要时用
        if (filter != null && filter.Length > 0 && mode != "folder") {
            try { Native.SetFileTypes(dlg, filter); } catch { }
        }

        // 关键：**把当前前台窗口当 owner**。
        // 传 IntPtr.Zero（无主窗口）时 Windows 不保证对话框置顶，实测会跑到浏览器后面，
        // 用户就以为"没弹出来"。挂到前台窗口上它就会显示在浏览器之上。
        IntPtr owner = GetForegroundWindow();
        int hr = dlg.Show(owner);
        if (hr != 0) { return ""; }          // 用户取消（或出错）

        IShellItem result;
        dlg.GetResult(out result);
        if (result == null) { return ""; }
        string path;
        result.GetDisplayName(0x80058000 /* SIGDN_FILESYSPATH */, out path);
        return path ?? "";
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SHCreateItemFromParsingName(
        string pszPath, IntPtr pbc, ref Guid riid, out object ppv);
}

public static class Native {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern int SHCreateItemFromParsingName(
        string pszPath, IntPtr pbc, ref Guid riid, out IntPtr ppv);

    public static void SetFileTypes(IFileDialog dlg, string spec) {
        // spec 形如 "Excel 表格|*.xlsx;*.xls"，多条用 ;; 分隔
        string[] parts = spec.Split(new[] { ";;" }, StringSplitOptions.RemoveEmptyEntries);
        var specs = new COMDLG_FILTERSPEC[parts.Length];
        var pinned = new IntPtr[parts.Length * 2];
        for (int i = 0; i < parts.Length; i++) {
            string[] kv = parts[i].Split('|');
            pinned[i * 2] = Marshal.StringToHGlobalUni(kv[0]);
            pinned[i * 2 + 1] = Marshal.StringToHGlobalUni(kv.Length > 1 ? kv[1] : "*.*");
            specs[i] = new COMDLG_FILTERSPEC();
            specs[i].pszName = pinned[i * 2];
            specs[i].pszSpec = pinned[i * 2 + 1];
        }
        IntPtr p = Marshal.AllocCoTaskMem(Marshal.SizeOf(typeof(COMDLG_FILTERSPEC)) * parts.Length);
        for (int i = 0; i < parts.Length; i++) {
            Marshal.StructureToPtr(specs[i], (IntPtr)((long)p + i * Marshal.SizeOf(typeof(COMDLG_FILTERSPEC))), false);
        }
        dlg.SetFileTypes((uint)parts.Length, p);
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct COMDLG_FILTERSPEC { public IntPtr pszName; public IntPtr pszSpec; }
}
"@

$mode    = "__MODE__"
$title   = "__TITLE__"
$initial = "__INITIAL__"
$filter  = "__FILTER__"

$path = [Pick]::Show($mode, $title, $initial, $filter)
if ($path) {
    Write-Output ("###PATH###" + $path)
} else {
    Write-Output "###PATH###"
}
"""


def _b64(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _ps_quote(text: str) -> str:
    """塞进 PowerShell 双引号字符串里：反引号、双引号、$ 都要转义。"""
    return (text or "").replace("`", "``").replace('"', '`"').replace("$", "`$")


def _run(mode: str, *, title: str, initial: str = "", filter_spec: str = "", timeout: int) -> str:
    """跑一次对话框，返回选中的路径（空字符串 = 用户取消）。"""
    script = (
        _PS_TEMPLATE.replace("__MODE__", _ps_quote(mode))
        .replace("__TITLE__", _ps_quote(title))
        .replace("__INITIAL__", _ps_quote(initial))
        .replace("__FILTER__", _ps_quote(filter_spec))
    )

    # CREATE_NO_WINDOW：别让 PowerShell 闪一个控制台出来抢焦点——
    # 控制台一旦成为前台窗口，对话框就会被挂到它下面，还是可能藏在浏览器后面。
    creation_flags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",                     # 对话框需要单线程单元
            "-EncodedCommand",
            _b64(script),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creation_flags,
    )

    for line in (result.stdout or "").splitlines():
        if line.startswith(MARKER):
            return line[len(MARKER):].strip()
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:400]
        raise RuntimeError(f"文件对话框调用失败（退出码 {result.returncode}）：{detail}")
    return ""


class DialogBusy(RuntimeError):
    """已经有一个对话框开着。"""


def _guarded(mode: str, *, title: str, initial: str = "", filter_spec: str = "",
             timeout: int = DEFAULT_TIMEOUT) -> str:
    if not _DIALOG_LOCK.acquire(blocking=False):
        raise DialogBusy("已经有一个文件对话框开着，先在那边选完或关掉它。")
    try:
        return _run(mode, title=title, initial=initial, filter_spec=filter_spec, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"文件对话框等了 {timeout} 秒还没关，已放弃。") from exc
    finally:
        _DIALOG_LOCK.release()


def pick_folder(*, title: str = "选择文件夹", initial: str | Path | None = None,
                timeout: int = DEFAULT_TIMEOUT) -> str:
    """弹系统「选择文件夹」对话框。取消返回空字符串。"""
    return _guarded("folder", title=title, initial=str(initial or ""), timeout=timeout)


def pick_file(*, title: str = "选择文件", initial: str | Path | None = None,
              filter_spec: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """弹系统「打开文件」对话框。filter_spec 形如 `Excel 表格|*.xlsx;*.xls`。"""
    return _guarded("file", title=title, initial=str(initial or ""),
                    filter_spec=filter_spec, timeout=timeout)


def save_file(*, title: str = "保存为", initial: str | Path | None = None,
              filter_spec: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """弹系统「保存」对话框。"""
    return _guarded("save", title=title, initial=str(initial or ""),
                    filter_spec=filter_spec, timeout=timeout)
