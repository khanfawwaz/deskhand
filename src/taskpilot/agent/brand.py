"""Give a copy of Python's windowless launcher its own identity, so Task Manager lists it as "Deskhand" (name,
description, company, icon) instead of a generic "Python".

    python -m taskpilot.agent.brand C:\\path\\Deskhand.exe

Windows only (uses the documented resource-update API through ctypes). The file is modified in place: only ever point it
at a COPY of pythonw.exe that belongs to Deskhand's own private runtime.
"""
from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes

RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16
LANG_EN_US = 0x0409


# --------------------------------------------------------------------------------- version resource (VS_VERSIONINFO)
def _pad(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def _block(key: str, value: bytes = b"", children: list[bytes] | None = None, *, text: bool = False, vlen: int = 0) -> bytes:
    head = _pad(struct.pack("<HHH", 0, vlen, 1 if text else 0) + (key + "\0").encode("utf-16-le"))
    body = _pad(value) if value else b""
    kids = b"".join(_pad(c) for c in (children or []))
    data = head + body + kids
    return struct.pack("<H", len(data)) + data[2:]


def version_resource(name: str, description: str, company: str, version: str = "1.0.0.0") -> bytes:
    parts = [int(x) for x in version.split(".")] + [0] * 4
    ms, ls = (parts[0] << 16) | parts[1], (parts[2] << 16) | parts[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x00040004, 1, 0, 0, 0)
    strings = {"CompanyName": company, "FileDescription": description, "FileVersion": version,
               "InternalName": name, "OriginalFilename": name + ".exe", "ProductName": name, "ProductVersion": version}
    entries = [_block(k, (v + "\0").encode("utf-16-le"), text=True, vlen=len(v) + 1) for k, v in strings.items()]
    table = _block("040904b0", children=entries, text=True)
    sfi = _block("StringFileInfo", children=[table], text=True)
    var = _block("Translation", struct.pack("<HH", LANG_EN_US, 0x04B0), vlen=4)
    vfi = _block("VarFileInfo", children=[var], text=True)
    return _block("VS_VERSION_INFO", fixed, [sfi, vfi], vlen=len(fixed))


# --------------------------------------------------------------------------------- icon resources
def _dib(img) -> bytes:
    """A 32-bit icon image as a DIB (BITMAPINFOHEADER + bottom-up BGRA + empty AND mask): works at every size."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.tobytes("raw", "BGRA")
    rows = [px[y * w * 4:(y + 1) * w * 4] for y in range(h)][::-1]
    mask = b"\0" * (((w + 31) // 32) * 4 * h)
    return struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0) + b"".join(rows) + mask


def icon_resources(sizes=(16, 24, 32, 48, 64, 128, 256)):
    from ..branding import draw_icon
    images = []
    for n in sizes:
        im = draw_icon(n)
        if n >= 256:                                      # PNG-compressed is the norm for the 256 px entry
            import io
            buf = io.BytesIO()
            im.save(buf, "PNG")
            images.append((n, buf.getvalue()))
        else:
            images.append((n, _dib(im)))
    group = struct.pack("<HHH", 0, 1, len(images))
    for i, (n, data) in enumerate(images, start=1):
        group += struct.pack("<BBBBHHIH", n % 256, n % 256, 0, 0, 1, 32, len(data), i)
    return group, images


# --------------------------------------------------------------------------------- the Win32 plumbing
def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.BeginUpdateResourceW.argtypes, k.BeginUpdateResourceW.restype = [wintypes.LPCWSTR, wintypes.BOOL], wintypes.HANDLE
    k.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
    k.UpdateResourceW.restype = wintypes.BOOL
    k.EndUpdateResourceW.argtypes, k.EndUpdateResourceW.restype = [wintypes.HANDLE, wintypes.BOOL], wintypes.BOOL
    k.LoadLibraryExW.argtypes, k.LoadLibraryExW.restype = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD], wintypes.HMODULE
    k.FreeLibrary.argtypes = [wintypes.HMODULE]
    return k


def _callback_types():
    """Windows-only callback signatures, built on demand so this module still imports (and tests run) on Linux."""
    name_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    lang_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p)
    return name_proc, lang_proc


def _existing(path: str, rtype: int) -> list[tuple[int, int]]:
    """(name id, language) of every resource of this type already in the file, so they can be removed first."""
    k = _k32()
    ENUMNAMEPROC, ENUMLANGPROC = _callback_types()
    h = k.LoadLibraryExW(path, None, 0x2)                 # LOAD_LIBRARY_AS_DATAFILE
    if not h:
        return []
    found: list[tuple[int, int]] = []
    names: list[int] = []

    @ENUMNAMEPROC
    def on_name(hmod, t, name, lp):
        if name is not None and name < 0x10000:           # integer ids only (IS_INTRESOURCE)
            names.append(name)
        return True

    @ENUMLANGPROC
    def on_lang(hmod, t, name, lang, lp):
        found.append((name or 0, lang))
        return True
    enum_names, enum_langs = k.EnumResourceNamesW, k.EnumResourceLanguagesW
    enum_names.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ENUMNAMEPROC, ctypes.c_void_p]
    enum_langs.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ENUMLANGPROC, ctypes.c_void_p]
    enum_names.restype = enum_langs.restype = wintypes.BOOL
    for restype in (rtype,):
        enum_names(h, restype, on_name, 0)
    for n in names:
        enum_langs(h, rtype, n, on_lang, 0)
    k.FreeLibrary(h)
    return found


def patch_exe(path: str, *, name: str = "Deskhand", description: str = "Deskhand", company: str = "Deskhand",
              version: str = "1.0.0.0") -> None:
    k = _k32()
    group, images = icon_resources()
    h = k.BeginUpdateResourceW(path, False)
    if not h:
        raise OSError(ctypes.get_last_error(), "cannot open the file for resource updates (is it running?)")
    ok = False
    try:
        def put(rtype, rid, lang, data):
            buf = ctypes.create_string_buffer(data, len(data)) if data is not None else None
            if not k.UpdateResourceW(h, rtype, rid, lang, buf, len(data) if data is not None else 0):
                raise OSError(ctypes.get_last_error(), f"UpdateResource failed for type {rtype} id {rid}")
        for rtype in (RT_VERSION, RT_GROUP_ICON, RT_ICON):
            for rid, lang in _existing(path, rtype):
                put(rtype, rid, lang, None)               # delete what Python shipped with
        put(RT_VERSION, 1, LANG_EN_US, version_resource(name, description, company, version))
        put(RT_GROUP_ICON, 1, LANG_EN_US, group)
        for i, (_, data) in enumerate(images, start=1):
            put(RT_ICON, i, LANG_EN_US, data)
        ok = True
    finally:
        if not k.EndUpdateResourceW(h, not ok):           # discard everything if anything failed
            raise OSError(ctypes.get_last_error(), "could not write the updated file")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m taskpilot.agent.brand <path to a COPY of pythonw.exe>")
    patch_exe(sys.argv[1])
    print("branded", sys.argv[1])
