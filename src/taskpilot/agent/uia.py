"""Windows UI Automation adapter: read an app's controls by NAME and act on them without guessing pixels.

Everything here is synchronous and safe to call from worker threads (each call initialises COM for its thread).
Acting prefers UIA patterns (Invoke / Toggle / SelectionItem / Value), which need no mouse and no focus; the
physical-mouse fallback is used only for controls that expose no pattern, and is reported as method="mouse".
Policy gating (which windows may be automated) is enforced by the CALLER before any act; see tools/ui_tools.py.
"""
from __future__ import annotations

import time
from typing import Any

import psutil

from ..titles import title_has

MAX_WALK = 2500          # hard cap on nodes visited per call
MAX_DEPTH = 40       # web pages nest very deeply: Chrome page content sits at depth 20-30
INTERACTIVE = {"Button", "Edit", "CheckBox", "RadioButton", "ComboBox", "ListItem", "MenuItem", "TabItem",
               "Hyperlink", "Slider", "Spinner", "SplitButton", "TreeItem", "DataItem", "Document"}
VALUE_ROLES = {"Edit", "ComboBox", "Document", "Spinner", "Slider"}
TOGGLE_ROLES = {"CheckBox", "RadioButton"}


def _auto():
    import uiautomation as auto
    return auto


class _Com:
    """Context manager: COM must be initialised once per thread that touches UIA."""
    def __enter__(self):
        self._ctx = _auto().UIAutomationInitializerInThread(debug=False)
        return self._ctx.__enter__() if hasattr(self._ctx, "__enter__") else self._ctx

    def __exit__(self, *a):
        if hasattr(self._ctx, "__exit__"):
            return self._ctx.__exit__(*a)


def _role(c) -> str:
    n = c.ControlTypeName or ""
    return n[:-7] if n.endswith("Control") else n


def _is_glyph(name: str) -> bool:
    """Icon fonts show up as private-use characters (e.g. '\\ue81c'): useless to a model."""
    return bool(name) and all(0xE000 <= ord(ch) <= 0xF8FF or ch.isspace() for ch in name)


def _proc_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _win_info(c) -> dict:
    return {"title": c.Name or "", "process": _proc_name(c.ProcessId), "pid": c.ProcessId,
            "handle": c.NativeWindowHandle}


def find_window(title: str) -> dict | None:
    """Top-level window whose title contains `title` (case-insensitive); empty = the foreground window."""
    auto = _auto()
    with _Com():
        if not title:
            fg = auto.GetForegroundControl()
            top = fg.GetTopLevelControl() if fg else None
            return _win_info(top) if top else None
        best = None
        for w in auto.GetRootControl().GetChildren():
            if title_has(w.Name or "", title):
                if not w.IsOffscreen:
                    return _win_info(w)
                best = best or w
        return _win_info(best) if best else None


def _resolve(win: dict):
    auto = _auto()
    c = auto.ControlFromHandle(win["handle"])
    if c is None or c.ProcessId != win["pid"]:
        raise LookupError("the window changed or closed since it was selected")
    return c


def _walk(root, include_offscreen: bool, limit: int = MAX_WALK) -> list[dict]:
    """Controls in true DOCUMENT order (pre-order, siblings top-to-bottom). Order matters: 'the first video in the
    list' must really be the first one on the page."""
    out: list[dict] = []
    try:
        top = root.GetChildren()
    except Exception:
        return out
    stack = [(c, 0) for c in reversed(top)]          # reversed so pop() yields them in order
    while stack and len(out) < limit:
        node, depth = stack.pop()
        try:
            role, name = _role(node), node.Name or ""
            off = bool(node.IsOffscreen)
        except Exception:
            continue
        if not (off and not include_offscreen):
            out.append({"ctrl": node, "role": role, "name": name, "id": node.AutomationId or "",
                        "enabled": bool(node.IsEnabled), "offscreen": off, "depth": depth})
        if depth < MAX_DEPTH:
            try:
                kids = node.GetChildren()
            except Exception:
                continue
            stack.extend((k, depth + 1) for k in reversed(kids))
    return out


def _value_of(el: dict) -> str | None:
    c = el["ctrl"]
    try:
        if el["role"] in VALUE_ROLES:
            vp = c.GetValuePattern()
            if vp is not None:
                return str(vp.Value)[:300]
        if el["role"] == "Text":
            return None
    except Exception:
        pass
    return None


def _state(el: dict) -> list[str]:
    st = []
    if not el["enabled"]:
        st.append("disabled")
    if el["role"] in TOGGLE_ROLES:
        try:
            tp = el["ctrl"].GetTogglePattern()
            if tp is not None and int(tp.ToggleState) == 1:
                st.append("checked")
        except Exception:
            pass
    try:
        if el["ctrl"].IsPassword:
            st.append("password")
    except Exception:
        pass
    return st


def _short(name: str, n: int = 90) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def _describe(el: dict, nth: int | None = None, name_len: int = 90) -> str:
    parts = [f'{el["role"]} "{_short(el["name"], name_len)}"' if el["name"] else el["role"]]
    if el["id"]:
        parts.append(f"id={el['id']}")
    v = _value_of(el)
    if v is not None:
        parts.append(f'value="{v}"')
    if nth and nth > 1:
        parts.append(f"#{nth}")
    parts += [f"[{s}]" for s in _state(el)]
    return " ".join(parts)


def _rect(el: dict):
    try:
        r = el["ctrl"].BoundingRectangle
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def _biggest_document(els: list[dict]):
    best, area = None, 0
    for e in els:
        if e["role"] == "Document":
            r = _rect(e)
            if r and (r[2] - r[0]) * (r[3] - r[1]) > area:
                best, area = r, (r[2] - r[0]) * (r[3] - r[1])
    return best


def _inside(el: dict, box) -> bool:
    r = _rect(el)
    return bool(r) and r[0] >= box[0] - 2 and r[1] >= box[1] - 2 and r[2] <= box[2] + 2 and r[3] <= box[3] + 2 \
        and (r[2] - r[0]) > 0


def _role_matches(want: str, have: str) -> bool:
    w = want.strip().lower().replace(" ", "")
    h = have.lower()
    return w == h or ROLE_ALIASES.get(w, "").lower() == h


def snapshot(win: dict, max_elements: int = 80, query: str = "", include_offscreen: bool = False,
             name_len: int = 90, role: str = "", skip: int = 0, min_chars: int = 0) -> dict:
    with _Com():
        root = _resolve(win)
        els = _walk(root, include_offscreen)
        q = query.lower()
        useful = [e for e in els if not _is_glyph(e["name"]) and (e["name"] or e["id"] or e["role"] in INTERACTIVE)]
        if q:
            useful = [e for e in useful if q in e["name"].lower() or q in e["id"].lower()]
        if role:
            useful = [e for e in useful if _role_matches(role, e["role"])]
        if min_chars:
            useful = [e for e in useful if len(e["name"]) >= min_chars]
        # Browsers: the web PAGE is what matters, not the toolbar. Rank controls inside the biggest Document first.
        doc = _biggest_document(els)
        in_page = (lambda e: _inside(e, doc)) if doc else (lambda e: False)
        inter = lambda e: e["role"] in INTERACTIVE
        # page controls first, then browser UI; within each: interactive controls, then named text (values)
        ranked = ([e for e in useful if in_page(e) and inter(e)] + [e for e in useful if in_page(e) and not inter(e)]
                  + [e for e in useful if not in_page(e) and inter(e)]
                  + [e for e in useful if not in_page(e) and not inter(e)])
        shown = ranked[skip:skip + max_elements]
        seen: dict[tuple, int] = {}
        counts: dict[tuple, int] = {}
        for e in useful:
            k = (e["role"], e["name"].lower())
            counts[k] = counts.get(k, 0) + 1
        lines = []
        for e in shown:
            k = (e["role"], e["name"].lower())
            seen[k] = seen.get(k, 0) + 1
            lines.append(_describe(e, seen[k] if counts[k] > 1 else None, name_len))
        r = win and root.BoundingRectangle
        return {"tree": "\n".join(lines), "count": len(useful), "shown": len(shown),
                "truncated": len(useful) > len(shown),
                "window": {"title": win["title"], "process": win["process"]},
                "rect": [r.left, r.top, r.right, r.bottom] if r else None,
                "hint": ("few controls are exposed by this app; use capture_screen / screen.zoom and click"
                         if len(useful) <= 3 else
                         (f"showing {skip + 1}-{skip + len(shown)} of {len(useful)}; pass skip={skip + len(shown)} for the next ones, "
                          "or role=Hyperlink / min_chars=25 (long names = content, not menu labels) / a query to narrow it" if len(useful) > skip + len(shown) else ""))}


def _norm_name(s: str) -> str:
    """Lowercase, collapse whitespace, and drop the trailing '…' that ui.snapshot adds to long names, so a model
    that copies a shortened name still matches (it is a prefix of the real one)."""
    s = " ".join(s.lower().split())
    return s.rstrip("…").rstrip(".").rstrip() if s.endswith(("…", "...")) else s


ROLE_ALIASES = {"link": "hyperlink", "a": "hyperlink", "textbox": "edit", "input": "edit", "textarea": "edit",
                "searchbox": "edit", "text field": "edit", "field": "edit", "tab": "tabitem", "menu item": "menuitem",
                "list item": "listitem", "radio": "radiobutton", "check box": "checkbox", "label": "text",
                "heading": "text", "image": "image", "img": "image"}


def _matches(els: list[dict], sel: dict) -> list[dict]:
    role = (sel.get("role") or "").lower().removesuffix("control")
    role = ROLE_ALIASES.get(role, role)
    name, ident = _norm_name(sel.get("name") or ""), (sel.get("id") or "").lower()
    pool = [e for e in els if not _is_glyph(e["name"])]
    if role:
        pool = [e for e in pool if e["role"].lower() == role]
    if ident:
        pool = [e for e in pool if e["id"].lower() == ident]
    if name:
        exact = [e for e in pool if _norm_name(e["name"]) == name]
        pool = exact or [e for e in pool if name in _norm_name(e["name"])]
    return pool


ACTIONABLE = frozenset({"Button", "Hyperlink", "TabItem", "MenuItem", "CheckBox", "RadioButton", "ListItem",
                        "SplitButton", "TreeItem", "DataItem", "ComboBox"})
EDITABLE = frozenset({"Edit", "ComboBox", "Document", "Spinner"})
PREFERRED = {"click": ACTIONABLE, "double_click": ACTIONABLE, "type": EDITABLE}


def choose(cands: list[dict], sel: dict, action: str, describe=None):
    """Pick one control from the matches, or explain why not. Shared by the real adapter and test fakes.

    On web pages the same words exist as a link AND the text inside it (several matches). For click/type the
    only candidate that can actually take the action wins, so the model need not guess a role. Genuine
    ambiguity (several clickable matches) still refuses to guess."""
    describe = describe or _describe
    if not cands:
        return None, {"error": f"no control matches {_sel_text(sel)}; call ui.snapshot to see the real names"}
    nth = sel.get("nth")
    pool = cands
    if len(cands) > 1 and (pref := PREFERRED.get(action)):
        narrowed = [c for c in cands if c["role"] in pref]
        if narrowed:
            pool = narrowed
    if nth is None and len(pool) > 1:
        shown = "; ".join(describe(c) for c in pool[:5])
        return None, {"error": f"{len(pool)} controls match {_sel_text(sel)}; add nth, role or id. Matches: {shown}"}
    idx = (nth or 1) - 1
    if idx >= len(pool):
        return None, {"error": f"only {len(pool)} controls match {_sel_text(sel)}, nth={nth} is out of range"}
    return pool[idx], None


def _pick(win: dict, sel: dict, action: str = "read", include_offscreen: bool = True):
    root = _resolve(win)
    return choose(_matches(_walk(root, include_offscreen), sel), sel, action)


def _sel_text(sel: dict) -> str:
    return ", ".join(f"{k}={sel[k]!r}" for k in ("role", "name", "id") if sel.get(k)) or "(empty selector)"


def _read(el: dict) -> tuple[str, str]:
    c = el["ctrl"]
    try:
        if c.IsPassword:
            raise PermissionError("password field: refusing to read it")
    except PermissionError:
        raise
    except Exception:
        pass
    try:
        vp = c.GetValuePattern()
        if vp is not None:
            return str(vp.Value)[:5000], "value"
    except Exception:
        pass
    try:
        tp = c.GetTextPattern()
        if tp is not None:
            return tp.DocumentRange.GetText(5000), "text"
    except Exception:
        pass
    return el["name"][:5000], "name"


def _escape_sendkeys(text: str) -> str:
    # uiautomation.SendKeys treats {Enter}, {Ctrl} ... as key names; make braces literal. A newline would press
    # Enter (which SENDS a chat message), so line breaks become spaces: sending is always an explicit, gated step.
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return "".join("{{}" if ch == "{" else "{}}" if ch == "}" else ch for ch in text)   # one pass: no double-escaping


def act(win: dict, sel: dict, action: str, text: str | None = None) -> dict:
    """action: click | double_click | type | read. Returns a dict; {'error': ...} on failure."""
    with _Com():
        el, err = _pick(win, sel, action)
        if err:
            return err
        c = el["ctrl"]
        desc = _describe(el)
        try:
            if action == "read":
                value, source = _read(el)
                return {"text": value, "source": source, "element": desc}

            if not el["enabled"]:
                return {"error": f"control is disabled: {desc}"}

            if action in ("click", "double_click"):
                if action == "click":
                    for getter, call, method in (
                            ("GetInvokePattern", lambda p: p.Invoke(), "invoke"),
                            ("GetTogglePattern", lambda p: p.Toggle(), "toggle"),
                            ("GetSelectionItemPattern", lambda p: p.Select(), "select"),
                            ("GetExpandCollapsePattern", lambda p: p.Expand(), "expand")):
                        try:
                            p = getattr(c, getter)()
                        except Exception:
                            p = None
                        if p is not None:
                            call(p)
                            return {"method": method, "element": desc}
                    c.Click(simulateMove=False)
                    return {"method": "mouse", "element": desc}
                c.DoubleClick(simulateMove=False)
                return {"method": "mouse", "element": desc}

            if action == "type":
                if "password" in _state(el):
                    return {"error": "password field: refusing to type into it"}
                value_pattern = None
                try:
                    value_pattern = c.GetValuePattern()
                except Exception:
                    pass
                if value_pattern is not None and not getattr(value_pattern, "IsReadOnly", False):
                    value_pattern.SetValue(text or "")
                    after = str(value_pattern.Value)
                    if after == (text or ""):
                        return {"method": "value", "element": desc, "readback_available": True,
                                "value_after": after[:2000]}
                    # Rich editors (chat composers) accept SetValue silently without showing it: type for real.
                c.SetFocus()
                time.sleep(0.15)
                c.SendKeys(_escape_sendkeys(text or ""), interval=0.01, waitTime=0.05)
                after = None
                try:
                    after = _read(el)[0][:2000]
                except Exception:
                    pass
                return {"method": "keyboard", "element": desc, "readback_available": after is not None,
                        "value_after": after}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)[:160]}", "element": desc}
        return {"error": f"unknown action '{action}'"}


STOP_NAMES = {"stop streaming", "stop generating", "stop response", "stop answer", "stop"}


def _select_blocks(els: list[dict], doc) -> list[str]:
    """Main-content text blocks in page order: no sidebar (left ~22% of the page: chat history lists), no
    duplicates, and no block that is merely a piece of a longer one."""
    left_cut = doc[0] + 0.22 * (doc[2] - doc[0]) if doc else None
    cands: list[str] = []
    for e in els:
        # Image/Button carry product-card titles ("Open <product> | Shop"). Hyperlink is deliberately NOT here:
        # chat sidebars are lists of hyperlinks (the user's private chat titles).
        if e["role"] not in ("Text", "Group", "ListItem", "Image", "Button"):
            continue
        n = " ".join(e["name"].split())
        if len(n) < 30 or "can make mistakes" in n.lower():
            continue
        if doc:
            r = _rect(e)
            if not r or not (r[0] >= doc[0] - 2 and r[2] <= doc[2] + 2 and r[1] >= doc[1] - 2 and r[3] <= doc[3] + 2):
                continue
            if r[2] <= left_cut:
                continue
        cands.append(n)
    uniq = list(dict.fromkeys(cands))
    return [n for n in uniq if not any(n != m and n in m for m in uniq)]


def _read_blocks(win: dict) -> tuple[list[str], bool]:
    with _Com():
        els = _walk(_resolve(win), True, limit=MAX_WALK)
        busy = any(e["role"] == "Button" and _norm_name(e["name"]) in STOP_NAMES for e in els)
        return _select_blocks(els, _biggest_document(els)), busy


def page_text(win: dict, min_wait_s: float = 6, quiet_s: float = 3, max_wait_s: float = 60, max_chars: int = 4000,
              _read=None, _sleep=time.sleep, _now=time.monotonic) -> dict:
    """Wait until the page's main text has stopped changing (and no 'Stop generating' button is showing), then
    return it. For answers that stream in: the model must not finish before the answer is complete."""
    read = _read or _read_blocks
    start = _now()
    last_hash, last_change = None, start
    while True:
        blocks, busy = read(win)
        h, now = hash(tuple(blocks)), _now()
        if h != last_hash:
            last_hash, last_change = h, now
        waited = now - start
        if not busy and waited >= min_wait_s and now - last_change >= quiet_s:
            stable = True
            break
        if waited >= max_wait_s:
            stable = False
            break
        _sleep(1.0)
    text = "\n".join(blocks)
    return {"page_text": text[-max_chars:], "chars": len(text), "truncated": len(text) > max_chars,
            "stable": stable, "still_generating": busy, "waited_s": round(_now() - start, 1)}


def wait_for(win: dict, sel: dict, timeout_s: float, gone: bool = False) -> dict:
    end = time.monotonic() + timeout_s
    with _Com():
        while True:
            try:
                root = _resolve(win)
                present = bool(_matches(_walk(root, True), sel))
            except LookupError:
                present = False
            if present != gone:
                return {"met": True, "waited_s": round(timeout_s - (end - time.monotonic()), 2)}
            if time.monotonic() >= end:
                return {"error": f"timed out after {timeout_s}s waiting for "
                                 f"{'disappearance' if gone else 'appearance'} of {_sel_text(sel)}"}
            time.sleep(0.25)
