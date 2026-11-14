"""Secret, credential, email, and local filesystem path redactor (`Redactor`).

Adapted from `ramenvr/autopilot/autopilot/redact.py` for Web, Mobile, and 3D traces,
reports, and blind vision payloads so artifacts never leak tokens or host paths.
"""

from __future__ import annotations

import collections
import getpass
import os
import re
from typing import Any

SECRET = "[secret]"

# Common API token / secret prefixes (OpenAI, Anthropic, GitHub, Google, Slack, Stripe, AWS, JWT)
TOKENS = re.compile(
    r"\b(?:"
    r"sk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_-]{16,}"
    r"|rk_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|pk_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|xox[baprs]-[0-9A-Za-z-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r")\b"
)
BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}")
SECRET_NAME = re.compile(r"(?i)(?:api[_-]?key|token|secret|password|passwd|auth|cookie|session|private[_-]?key)")
ASSIGNED = re.compile(
    r"(?i)\b(?P<key>[a-z0-9_]*(?:api_key|apikey|token|secret|password|passwd|auth_header)[a-z0-9_]*)"
    r"(?P<sep>\s*[:=]\s*[\"']?)(?P<val>[^\s\"',;}{]{8,})"
)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
HOME = re.compile(r"(?:[A-Za-z]:\\Users\\[^\\/\s\"']+|/(?:Users|home)/[^/\s\"']+)")


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.path.basename(os.path.expanduser("~"))


class Redactor:
    """Scrubs secrets, tokens, emails, home directories, and project roots from text and JSON."""

    def __init__(
        self,
        project_root: str | None = None,
        extra_secrets: list[str] | None = None,
    ) -> None:
        self.counts: collections.Counter[str] = collections.Counter()
        self.secrets = [s for s in (extra_secrets or []) if isinstance(s, str) and len(s) >= 6]
        self.project_patterns: list[re.Pattern[str]] = []
        if project_root and len(project_root) > 3:
            norm = os.path.abspath(project_root).rstrip("\\/")
            variants = {norm, norm.replace("\\", "/"), norm.replace("/", "\\")}
            for v in sorted(variants, key=len, reverse=True):
                self.project_patterns.append(re.compile(re.escape(v), re.I))

        home = os.path.expanduser("~")
        self.home_patterns: list[re.Pattern[str]] = []
        if home and len(home) > 3:
            for v in sorted({home, home.replace("\\", "/"), home.replace("/", "\\")}, key=len, reverse=True):
                self.home_patterns.append(re.compile(re.escape(v), re.I))

        user = _current_user()
        self.user_pattern = (
            re.compile(rf"(?<![A-Za-z0-9_]){re.escape(user)}(?![A-Za-z0-9_])", re.I)
            if user and len(user) >= 4 and user.lower() not in {"user", "root", "admin", "test"}
            else None
        )

    def text(self, value: str) -> str:
        """Return `value` with credentials, emails, and host/project paths redacted."""
        if not value:
            return value
        out = value
        for s in self.secrets:
            if s in out:
                self.counts["secret"] += out.count(s)
                out = out.replace(s, SECRET)
        out = self._sub(TOKENS, SECRET, out, "secret")
        out = self._sub(BEARER, lambda m: m.group(1) + SECRET, out, "secret")
        out = self._sub(ASSIGNED, self._replace_assigned, out, "secret")
        for pat in self.project_patterns:
            out = self._sub(pat, "<project>", out, "path")
        for pat in self.home_patterns:
            out = self._sub(pat, "~", out, "home")
        out = self._sub(HOME, "~", out, "home")
        out = self._sub(EMAIL, "[email]", out, "email")
        if self.user_pattern is not None:
            out = self._sub(self.user_pattern, "[user]", out, "user")
        return out

    def data(self, value: Any, key_name: str = "") -> Any:
        """Recursively redact a JSON-compatible Python structure."""
        if isinstance(value, str):
            if key_name and SECRET_NAME.search(key_name) and len(value) >= 8 and value != SECRET:
                self.counts["secret"] += 1
                return SECRET
            return self.text(value)
        if isinstance(value, dict):
            return {
                (self.text(k) if isinstance(k, str) else k): self.data(v, k if isinstance(k, str) else "")
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.data(item, key_name) for item in value]
        if isinstance(value, tuple):
            return [self.data(item, key_name) for item in value]
        return value

    def _replace_assigned(self, match: re.Match[str]) -> str:
        val = match.group("val")
        if val == SECRET or val.startswith("[") or val.isdigit():
            return match.group(0)
        return f"{match.group('key')}{match.group('sep')}{SECRET}"

    def _sub(self, pattern: re.Pattern[str], repl: Any, text: str, kind: str) -> str:
        out, n = pattern.subn(repl, text)
        if n:
            self.counts[kind] += n
        return out
