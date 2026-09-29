"""Structured errors. Every failure carries a machine-readable code and a concrete `fix`
so an agent can recover without asking a human (or knows exactly what to ask)."""

from __future__ import annotations

from typing import Any


class AsamError(Exception):
    def __init__(self, code: str, message: str, fix: str | None = None, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fix:
            d["fix"] = self.fix
        if self.details is not None:
            d["details"] = self.details
        return d

    def __str__(self) -> str:
        s = f"[{self.code}] {self.message}"
        if self.fix:
            s += f"\nFIX: {self.fix}"
        return s


def not_authenticated(extra: str = "") -> AsamError:
    return AsamError(
        "NOT_AUTHENTICATED",
        "No Google credentials found." + (f" {extra}" if extra else ""),
        fix=(
            "Run `asam auth status` to see what was checked. Easiest path: "
            "`asam auth login` (OAuth, one browser click). Headless/CI: `asam auth use <service-account.json>`. "
            "See `asam guide auth` for the 3-minute Google Cloud setup."
        ),
    )
