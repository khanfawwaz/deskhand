"""Deterministic permission policy. Used by the server AND re-enforced by the agent.

Nothing here consults the model: a proposal is data to be judged, never an instruction.
"""
from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass, field
from typing import Literal

from .protocol import (
    Action, AppToolCall, Click, DoubleClick, GrantAccess, Hotkey, LaunchApplication, PressKey, RunCommand,
    Scroll, TypeText,
)

Verdict = Literal["allow", "confirm", "deny"]

# Windows where automated input must never go: shells (would bypass the command allowlist), credential
# stores, elevation prompts, system management consoles.
DEFAULT_UI_DENY_PROCESSES = [
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe", "regedit.exe",
    "mmc.exe", "taskmgr.exe", "consent.exe", "logonui.exe", "credwiz.exe", "securityhealthsystray.exe",
    "1password.exe", "keepass.exe", "keepassxc.exe", "bitwarden.exe", "lastpass.exe", "dashlane.exe",
]
DEFAULT_UI_CONFIRM_NAMES = [
    "send", "send message", "send prompt", "send email", "reply", "delete", "remove", "discard", "submit", "post", "publish",
    "pay", "pay now", "buy", "buy now", "purchase", "place order", "confirm", "empty trash", "unsubscribe",
]
DEFAULT_UI_DENY_TITLES = ["user account control", "windows security", "credential manager", "task manager",
                          "registry editor", "password manager"]

# Sites Deskhand never operates, even if asked: sign-in, payment and credential pages.
DEFAULT_DENY_HOSTS = ["accounts.google.com", "login.live.com", "login.microsoftonline.com", "paypal.com", "stripe.com",
                      "bitwarden.com", "1password.com", "lastpass.com", "dashlane.com", "appleid.apple.com"]
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}
# Big companies host unrelated, PRIVATE services under one domain (mail.google.com, drive.google.com...). Approving
# "google.com" for a search must never approve Gmail, so these are approved one exact host at a time.
SHARED_DOMAINS = {"google.com", "microsoft.com", "live.com", "office.com", "apple.com", "icloud.com", "amazon.com",
                  "yahoo.com", "facebook.com", "meta.com", "linkedin.com", "github.com", "dropbox.com", "outlook.com"}


def is_shared(host: str) -> bool:
    h = (host or "").lower().strip(".")
    return any(h == d or h.endswith("." + d) for d in SHARED_DOMAINS)


def base_domain(host: str) -> str:
    """music.youtube.com -> youtube.com, www.bbc.co.uk -> bbc.co.uk: what a user thinks of as 'the site'."""
    h = (host or "").lower().strip(".")
    if is_shared(h):
        return h                                           # exact host: www.google.com is not mail.google.com
    labels = [x for x in h.split(".") if x]
    if labels and labels[0] == "www":
        labels = labels[1:]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())[:40]


KNOWN_KEYS = {
    *"abcdefghijklmnopqrstuvwxyz0123456789",
    "enter", "tab", "esc", "escape", "space", "backspace", "delete", "up", "down",
    "left", "right", "home", "end", "pageup", "pagedown",
    *(f"f{i}" for i in range(1, 13)),
    "ctrl", "shift", "alt", "win",
}
MODIFIERS = {"ctrl", "shift", "alt", "win"}
# Combos that are never allowed (system / security impact)
DENIED_HOTKEYS = [
    {"ctrl", "alt", "delete"}, {"alt", "f4"}, {"win", "r"}, {"win", "l"},
    {"ctrl", "shift", "esc"}, {"win", "x"},
]
# Combos that need a human to approve
CONFIRM_HOTKEYS = [{"ctrl", "w"}, {"ctrl", "shift", "delete"}]

DEFAULT_DENIED_ARG_PATTERNS = [
    r"(?i)^/?-?-?(delete|remove|rm|format|shutdown|restart|reg|del|rd|rmdir)$",
    r"(?i)^-(rf|fr|force)$",
]


@dataclass
class Decision:
    verdict: Verdict
    reason: str = ""
    # Set when a "deny" is really "not approved YET": {"kind": "app"|"site"|"window", "target": ...}. The orchestrator
    # then asks the user instead of failing the task. Never set for rules that must not be overridden.
    access: dict | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"


@dataclass
class CommandRule:
    executable: str                      # bare name, case-insensitive (e.g. "git")
    allowed_subcommands: list[str] | None = None   # first arg must be one of these
    confirm: bool = False
    denied_arg_patterns: list[str] = field(default_factory=list)


@dataclass
class Policy:
    approved_apps: dict[str, str] = field(default_factory=dict)       # app_id -> exe path/URI
    command_rules: list[CommandRule] = field(default_factory=list)
    allowed_dirs: list[str] = field(default_factory=list)
    confirm_tools: set[str] = field(default_factory=set)
    max_type_len: int = 500
    max_file_bytes: int = 1_000_000
    allowed_domains: list[str] = field(default_factory=list)   # empty = any http(s) host
    # UI automation / input gates. An app is allowed when its process is an approved app's exe, its window
    # title contains an approved app id or an entry of ui_allowed_windows. UWP apps (Calculator, Settings...)
    # live in ApplicationFrameHost.exe, so the TITLE is what identifies them.
    ui_allowed_windows: list[str] = field(default_factory=list)
    ui_allowed_hosts: list[str] = field(default_factory=list)   # browser windows showing these sites (e.g. chatgpt.com)
    ui_extra_processes: list[str] = field(default_factory=list)
    ui_deny_processes: list[str] = field(default_factory=lambda: list(DEFAULT_UI_DENY_PROCESSES))
    ui_deny_titles: list[str] = field(default_factory=lambda: list(DEFAULT_UI_DENY_TITLES))
    restrict_input_to_approved: bool = False   # also gate click/type/hotkey/scroll on the foreground app
    # Privacy mode for hosted models: only APPROVED windows may be shown to the model. Screenshots are cropped
    # to the approved foreground window (and refused for any other), and window lists/titles are filtered.
    cloud_safe: bool = False
    allow_private_windows: bool = False      # browser.open_private is refused unless explicitly enabled
    page_wait_s: float = 8.0                 # browser.open_url waits up to this long for the page's window to appear
    # When on, opening a website that was never approved is a request the USER answers (allow once / always / no),
    # not a silent failure. The laptop's helper turns this on; the user's answer is the only way a site gets added.
    require_site_approval: bool = False
    deny_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_HOSTS))
    # ui.click on a control with one of these names needs a human approval (sending, deleting, paying are
    # irreversible, so they never rest on the model's judgement alone).
    ui_confirm_names: list[str] = field(default_factory=lambda: list(DEFAULT_UI_CONFIRM_NAMES))
    allow_admin: bool = False  # always False in v1; kept explicit for audit
    # Sharing the computer with its owner: wait for a pause before taking the mouse/keyboard/front window, and put the
    # owner's window back afterwards.
    polite: bool = True
    polite_idle_s: float = 2.5               # how long the owner must have been still
    polite_max_wait_s: float = 45.0          # give up (and say so) after waiting this long

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        rules = [CommandRule(**r) for r in d.get("command_rules", [])]
        return cls(
            approved_apps=dict(d.get("approved_apps", {})),
            command_rules=rules,
            allowed_dirs=list(d.get("allowed_dirs", [])),
            confirm_tools=set(d.get("confirm_tools", [])),
            max_type_len=int(d.get("max_type_len", 500)),
            max_file_bytes=int(d.get("max_file_bytes", 1_000_000)),
            allowed_domains=list(d.get("allowed_domains", [])),
            ui_allowed_windows=list(d.get("ui_allowed_windows", [])),
            ui_allowed_hosts=[h.lower() for h in d.get("ui_allowed_hosts", [])],
            ui_extra_processes=list(d.get("ui_extra_processes", [])),
            ui_deny_processes=list(d.get("ui_deny_processes", DEFAULT_UI_DENY_PROCESSES)),
            ui_deny_titles=list(d.get("ui_deny_titles", DEFAULT_UI_DENY_TITLES)),
            restrict_input_to_approved=bool(d.get("restrict_input_to_approved", False)),
            cloud_safe=bool(d.get("cloud_safe", False)),
            allow_private_windows=bool(d.get("allow_private_windows", False)),
            page_wait_s=float(d.get("page_wait_s", 8.0)),
            require_site_approval=bool(d.get("require_site_approval", False)),
            deny_hosts=[h.lower() for h in d.get("deny_hosts", DEFAULT_DENY_HOSTS)],
            ui_confirm_names=list(d.get("ui_confirm_names", DEFAULT_UI_CONFIRM_NAMES)),
            polite=bool(d.get("polite", True)),
            polite_idle_s=float(d.get("polite_idle_s", 2.5)),
            polite_max_wait_s=float(d.get("polite_max_wait_s", 45.0)),
        )

    def ui_window_allowed(self, title: str, process: str, host: str | None = None) -> "Decision":
        """May the agent automate / type into this top-level window? Deny list beats allow list."""
        t, p = (title or "").lower(), ntpath.basename(process or "").lower()
        if p in {x.lower() for x in self.ui_deny_processes} or any(x.lower() in t for x in self.ui_deny_titles):
            return Decision("deny", f"'{title}' ({process}) is a protected window")
        approved_exes = {ntpath.basename(v).lower() for v in self.approved_apps.values()}
        approved_exes |= {x.lower() for x in self.ui_extra_processes}
        if p and p in approved_exes:
            return Decision("allow")
        if any(w.lower() in t for w in self.ui_allowed_windows) or any(a.lower() in t for a in self.approved_apps):
            return Decision("allow")
        if host and self.site_allowed(host):
            return Decision("allow")
        return Decision("deny", f"window '{title}' ({process}) is not an approved application",
                        access={"kind": "window", "target": title, "process": ntpath.basename(process or "")})

    # ------------------------------------------------------------ user-granted access
    def host_denied(self, host: str) -> bool:
        h = (host or "").lower()
        return any(h == d or h.endswith("." + d) for d in self.deny_hosts)

    def site_allowed(self, host: str) -> bool:
        h = (host or "").lower()
        return any(h == a or (h.endswith("." + a) and not is_shared(a)) for a in self.ui_allowed_hosts)

    def check_grant(self, kind: str, target: str) -> Decision:
        """May the user's approval be applied? Protected things stay protected whatever anyone says."""
        t = (target or "").lower()
        if kind == "site" and self.host_denied(t):
            return Decision("deny", f"Deskhand never operates sign-in or payment pages ({target})")
        if kind == "window" and (ntpath.basename(t) in {x.lower() for x in self.ui_deny_processes}
                                 or any(x.lower() in t for x in self.ui_deny_titles)):
            return Decision("deny", f"'{target}' is a protected window")
        if kind == "app" and _slug(target) in {ntpath.basename(x).lower().removesuffix(".exe") for x in self.ui_deny_processes}:
            return Decision("deny", f"'{target}' is a protected application")
        return Decision("allow")

    def grant(self, kind: str, target: str, value: str | None = None) -> str:
        """Add an approved site / app / window. Returns the normalised key stored (use it to revoke)."""
        if kind == "site":
            key = base_domain(target)
            if key not in self.ui_allowed_hosts:
                self.ui_allowed_hosts.append(key)
        elif kind == "app":
            key = _slug(target)
            self.approved_apps[key] = value or target
        else:                                              # window: approve the program, not one window title
            key = ntpath.basename(value or target).lower()
            if key and key not in {x.lower() for x in self.ui_extra_processes}:
                self.ui_extra_processes.append(key)
        return key

    def revoke(self, kind: str, key: str) -> None:
        if kind == "site":
            self.ui_allowed_hosts = [h for h in self.ui_allowed_hosts if h != key]
        elif kind == "app":
            self.approved_apps.pop(key, None)
        else:
            self.ui_extra_processes = [x for x in self.ui_extra_processes if x.lower() != key]

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _norm(p: str) -> str:
        return ntpath.normcase(ntpath.normpath(p))

    def dir_allowed(self, cwd: str) -> bool:
        if not ntpath.isabs(cwd) or ".." in re.split(r"[\\/]", cwd):
            return False
        c = self._norm(cwd)
        for root in self.allowed_dirs:
            r = self._norm(root)
            if c == r or c.startswith(r.rstrip("\\") + "\\"):
                return True
        return False

    def _rule_for(self, exe: str) -> CommandRule | None:
        # Only bare executable names are accepted: no path, no extension games.
        if re.search(r"[\\/:]", exe):
            return None
        name = exe.lower().removesuffix(".exe")
        for r in self.command_rules:
            if r.executable.lower().removesuffix(".exe") == name:
                return r
        return None

    # ------------------------------------------------------------ evaluate
    def evaluate(self, a: Action) -> Decision:
        d = self._evaluate(a)
        if d.verdict == "allow" and a.tool in self.confirm_tools:
            return Decision("confirm", f"policy requires approval for {a.tool}")
        return d

    def _evaluate(self, a: Action) -> Decision:
        if isinstance(a, LaunchApplication):
            if a.app_id not in self.approved_apps:
                return Decision("deny", f"application '{a.app_id}' is not approved",
                                access={"kind": "app", "target": a.app_id})
        elif isinstance(a, GrantAccess):
            return self.check_grant(a.kind, a.target)
        elif isinstance(a, AppToolCall):
            return self._app_tool(a)
        elif isinstance(a, RunCommand):
            return self._command(a)
        elif isinstance(a, TypeText):
            if len(a.text) > self.max_type_len:
                return Decision("deny", "text exceeds configured maximum length")
        elif isinstance(a, PressKey):
            k = a.key.lower()
            if k not in KNOWN_KEYS:
                return Decision("deny", f"unsupported key '{a.key}'")
        elif isinstance(a, Hotkey):
            keys = [k.lower() for k in a.keys]
            if any(k not in KNOWN_KEYS for k in keys):
                return Decision("deny", "unsupported key in combination")
            if len(set(keys)) != len(keys) or not (set(keys) & MODIFIERS):
                return Decision("deny", "hotkey must be a distinct modifier+key combination")
            s = set(keys)
            if any(bad <= s for bad in DENIED_HOTKEYS):
                return Decision("deny", "hotkey is prohibited by policy")
            if any(c <= s for c in CONFIRM_HOTKEYS):
                return Decision("confirm", "hotkey may discard user data")
        elif isinstance(a, (Click, DoubleClick, Scroll)):
            if a.x < 0 or a.y < 0:
                return Decision("deny", "negative coordinates")
        return Decision("allow")

    def _app_tool(self, a: AppToolCall) -> Decision:
        from .tools import get_registry   # lazy: the registry imports this module
        tool = get_registry().get(a.name)
        if tool is None:
            return Decision("deny", f"unknown tool '{a.name}'")
        try:
            args = tool.params.model_validate(a.args)
        except Exception as e:           # pydantic.ValidationError: keep the reason short
            return Decision("deny", f"invalid arguments for {a.name}: {str(e).splitlines()[0][:120]}")
        return tool.check(args, self) if tool.check else Decision("allow")

    def _command(self, a: RunCommand) -> Decision:
        rule = self._rule_for(a.executable)
        if rule is None:
            return Decision("deny", f"command '{a.executable}' is not on the allowlist")
        if not self.dir_allowed(a.cwd):
            return Decision("deny", "working directory is not authorized")
        if any(c in arg for arg in a.args for c in "\x00\r\n"):
            return Decision("deny", "control characters in arguments")
        if rule.allowed_subcommands is not None:
            if not a.args or a.args[0] not in rule.allowed_subcommands:
                return Decision("deny", f"subcommand not permitted for {rule.executable}")
        pats = DEFAULT_DENIED_ARG_PATTERNS + rule.denied_arg_patterns
        for arg in a.args:
            if any(re.search(p, arg) for p in pats):
                return Decision("deny", f"argument '{arg}' matches a prohibited pattern")
        if rule.confirm:
            return Decision("confirm", f"{rule.executable} requires approval")
        return Decision("allow")


# Goals that should never start silently. Deterministic and deliberately broad: a false alarm costs
# one confirmation click, a miss costs a wiped machine. This is not a substitute for the action policy.
_RISKY_GOAL = re.compile(
    r"(?i)\b(shut\s?down|power\s?off|reboot|restart (the )?(computer|pc|machine|system)|log\s?off|sign out|"
    r"format( the)? (drive|disk|c:)|wipe|factory reset|erase (everything|all|the disk)|"
    r"delete (all|everything|the (whole|entire))|rm\s+-rf|uninstall|"
    r"(disable|turn off) (the )?(defender|firewall|antivirus|windows security|uac)|"
    r"(change|reset|reveal|show|send|enter) (my |the )?(password|passcode|pin|2fa|api key|token|seed phrase)|"
    r"(send|transfer|wire|pay|buy|purchase|sell|trade)\b.{0,30}\b(money|funds|crypto|bitcoin|usd|\$|shares|stock))"
)


def risk_screen(goal: str) -> str | None:
    """Returns why a goal needs explicit approval before the task starts, or None."""
    m = _RISKY_GOAL.search(goal)
    return f"goal mentions a high-impact operation ('{m.group(0).strip()}')" if m else None
