"""
Windows native file/folder dialog helper using IFileOpenDialog COM.
Called as subprocess from server.py for modern Explorer-style dialogs.

Usage: python _win_dialog.py <mode> [initial_dir] [title]
  mode: "files" or "folder"
Output: selected path(s), one per line. Empty output = cancelled.
"""
import ctypes
import ctypes.wintypes as _wt
import os
import sys
import uuid

# ── COM GUID helper ──────────────────────────────────────────────────────────
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8),
    ]

def _guid(s):
    u = uuid.UUID(s)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[:3]
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g

# ── COM identifiers ─────────────────────────────────────────────────────────
_CLSID_FileOpenDialog = _guid("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
_IID_IFileOpenDialog  = _guid("{d57c7288-d4ad-4768-be02-9d969532d960}")
_IID_IShellItem       = _guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")

# ── IFileDialog option flags ─────────────────────────────────────────────────
_FOS_PICKFOLDERS      = 0x00000020
_FOS_FORCEFILESYSTEM  = 0x00000040
_FOS_ALLOWMULTISELECT = 0x00000200
_SIGDN_FILESYSPATH    = 0x80058000

# ── DLL handles ──────────────────────────────────────────────────────────────
_ole32   = ctypes.windll.ole32
_shell32 = ctypes.windll.shell32
_user32  = ctypes.windll.user32

_shell32.SHCreateItemFromParsingName.argtypes = [
    _wt.LPCWSTR, ctypes.c_void_p,
    ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
]
_shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None

# ── VTable slots (IFileOpenDialog inherits IUnknown→IModalWindow→IFileDialog)
# IUnknown:      QI(0)  AddRef(1)  Release(2)
# IModalWindow:  Show(3)
# IFileDialog:   SetFileTypes(4) … SetOptions(9) … SetFolder(12) … SetTitle(17) … GetResult(20)
# IFileOpenDialog: GetResults(27)
#
# IShellItem:    … GetDisplayName(5)
# IShellItemArray: … GetCount(7)  GetItemAt(8)

def _vt(ptr, slot):
    """Get a vtable function pointer by slot index."""
    vt = ctypes.cast(
        ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0],
        ctypes.POINTER(ctypes.c_void_p),
    )
    return vt[slot]


def _call(ptr, slot, restype, *argtypes_and_args):
    """Generic COM vtable call: _call(obj, slot, HRESULT, (type1, val1), (type2, val2), ...)"""
    argtypes = [ctypes.c_void_p] + [t for t, _ in argtypes_and_args]
    args = [ptr] + [v for _, v in argtypes_and_args]
    ft = ctypes.CFUNCTYPE(restype, *argtypes)
    return ft(_vt(ptr, slot))(*args)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "folder"
    initial_dir = sys.argv[2] if len(sys.argv) > 2 else ""
    title = sys.argv[3] if len(sys.argv) > 3 else ""

    _ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    try:
        # Create IFileOpenDialog
        pDlg = ctypes.c_void_p()
        hr = _ole32.CoCreateInstance(
            ctypes.byref(_CLSID_FileOpenDialog), None, 0x1,
            ctypes.byref(_IID_IFileOpenDialog), ctypes.byref(pDlg),
        )
        if hr != 0:
            print(f"ERR:CoCreateInstance 0x{hr & 0xFFFFFFFF:08X}", file=sys.stderr)
            sys.exit(1)

        # ── Declare COM method helpers using CFUNCTYPE ────────────────────
        def Release(p):
            ft = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
            return ft(_vt(p, 2))(p)

        def Show(p, hwnd):
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
            return ft(_vt(p, 3))(p, hwnd)

        def SetOptions(p, opts):
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint)
            return ft(_vt(p, 9))(p, opts)

        def SetFolder(p, psi):
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
            return ft(_vt(p, 12))(p, psi)

        def SetTitle(p, title_str):
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p)
            return ft(_vt(p, 17))(p, title_str)

        def GetResult(p):
            psi = ctypes.c_void_p()
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            ft(_vt(p, 20))(p, ctypes.byref(psi))
            return psi

        def GetResults(p):
            psia = ctypes.c_void_p()
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            ft(_vt(p, 27))(p, ctypes.byref(psia))
            return psia

        def SIA_GetCount(psia):
            n = ctypes.c_uint(0)
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint))
            ft(_vt(psia, 7))(psia, ctypes.byref(n))
            return n.value

        def SIA_GetItemAt(psia, idx):
            psi = ctypes.c_void_p()
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
            ft(_vt(psia, 8))(psia, idx, ctypes.byref(psi))
            return psi

        def SI_GetDisplayName(psi, sigdn):
            psz = ctypes.c_wchar_p()
            ft = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_wchar_p))
            ft(_vt(psi, 5))(psi, sigdn, ctypes.byref(psz))
            return psz

        # ── Configure dialog ─────────────────────────────────────────────
        if mode == "folder":
            SetOptions(pDlg, _FOS_PICKFOLDERS | _FOS_FORCEFILESYSTEM)
        else:
            SetOptions(pDlg, _FOS_FORCEFILESYSTEM | _FOS_ALLOWMULTISELECT)

        if title:
            SetTitle(pDlg, title)

        if initial_dir and os.path.isdir(initial_dir):
            pFolder = ctypes.c_void_p()
            hr2 = _shell32.SHCreateItemFromParsingName(
                ctypes.c_wchar_p(initial_dir), None,
                ctypes.byref(_IID_IShellItem), ctypes.byref(pFolder),
            )
            if hr2 == 0 and pFolder:
                SetFolder(pDlg, pFolder)
                Release(pFolder)

        # Get foreground window to parent the dialog (ensures it comes to front)
        hwnd = _user32.GetForegroundWindow() or 0

        # ── Show dialog ──────────────────────────────────────────────────
        hr_show = Show(pDlg, hwnd)
        if hr_show != 0:
            Release(pDlg)
            sys.exit(0)  # User cancelled

        # ── Extract results ──────────────────────────────────────────────
        if mode == "folder":
            pResult = GetResult(pDlg)
            psz = SI_GetDisplayName(pResult, _SIGDN_FILESYSPATH)
            if psz.value:
                sys.stdout.buffer.write((psz.value + "\n").encode("utf-8"))
            _ole32.CoTaskMemFree(psz)
            Release(pResult)
        else:
            pResults = GetResults(pDlg)
            count = SIA_GetCount(pResults)
            for i in range(count):
                pItem = SIA_GetItemAt(pResults, i)
                psz = SI_GetDisplayName(pItem, _SIGDN_FILESYSPATH)
                if psz.value:
                    sys.stdout.buffer.write((psz.value + "\n").encode("utf-8"))
                _ole32.CoTaskMemFree(psz)
                Release(pItem)
            Release(pResults)

        Release(pDlg)
    finally:
        _ole32.CoUninitialize()


if __name__ == "__main__":
    main()
