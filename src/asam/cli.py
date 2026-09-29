"""CLI generated from the op registry. JSON on stdout (errors too, with a `fix`), exit code 1 on error.

  asam doctor                          # diagnose everything
  asam report example.com --md         # full analysis, print Markdown
  asam gsc query --site example.com --dimensions query,page --filters "query contains shoes"
  asam mcp                             # run as MCP server (stdio)
"""

import argparse
import inspect
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any

from . import __version__
from .errors import AsamError
from .ops import REGISTRY, Op

SHORTCUTS = {"doctor": "doctor.run", "guide": "guide.show", "report": "analyze.report", "sites": "gsc.sites",
             "audit": "web.audit", "crawl": "web.crawl", "pagespeed": "web.pagespeed"}
NO_SPLIT = {"filters"}
# optional params that read better as positionals: `asam report example.com`, `asam guide auth`
OPTIONAL_POSITIONAL = {("guide.show", "topic"), ("analyze.report", "site"), ("doctor.run", "site"),
                       ("config.set", "value"), ("indexnow.submit", "urls"), ("gsc.inspect_batch", "urls")}


def _base(tp: Any) -> tuple[Any, bool]:
    """Return (base type, is_list) for annotations like `list[str] | None`."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _base(args[0]) if args else (str, False)
    if origin in (list, typing.List):  # noqa: UP006
        return (typing.get_args(tp) or (str,))[0], True
    return tp, False


def _add_op_args(p: argparse.ArgumentParser, o: Op) -> None:
    """First required param (and a few optional ones) become positionals. Every positional is ALSO accepted as
    --name, so `asam gsc inspect URL` and `asam gsc inspect --url URL` both work."""
    hints = typing.get_type_hints(o.fn)
    first = True
    for name, param in inspect.signature(o.fn).parameters.items():
        tp, is_list = _base(hints.get(name, str))
        required = param.default is inspect.Parameter.empty
        flag = "--" + name.replace("_", "-")
        conv = tp if tp in (int, float) else str
        if (first and required) or (o.name, name) in OPTIONAL_POSITIONAL:
            p.add_argument(name, nargs="*" if is_list else "?", type=conv, metavar=name.upper(),
                           default=None if required else param.default)
            p.add_argument(flag, dest=f"_opt_{name}", action="append" if is_list else "store", type=conv,
                           default=None, help=argparse.SUPPRESS)
            first = False
            continue
        first = False
        if tp is bool:
            p.add_argument(flag, dest=name, action=argparse.BooleanOptionalAction, default=param.default)
        elif is_list:
            p.add_argument(flag, dest=name, action="append", default=None, required=required,
                           help="repeatable" + ("" if name in NO_SPLIT else "; or comma-separated"))
        else:
            p.add_argument(flag, dest=name, type=conv, default=None if required else param.default,
                           required=required)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="asam", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"asam {__version__}")
    ap.add_argument("--compact", action="store_true", help="single-line JSON")
    sub = ap.add_subparsers(dest="group", metavar="<command>")
    sub.add_parser("mcp", help="run the MCP server over stdio (for Claude Code / any MCP client)")
    groups: dict[str, argparse._SubParsersAction] = {}
    for short, target in SHORTCUTS.items():
        o = REGISTRY[target]
        sp = sub.add_parser(short, help=f"= {o.group} {o.action}: {o.doc.splitlines()[0] if o.doc else ''}",
                            description=o.doc, formatter_class=argparse.RawDescriptionHelpFormatter)
        _add_op_args(sp, o)
        if target == "analyze.report":
            sp.add_argument("--md", action="store_true", help="print the Markdown report instead of JSON")
        sp.set_defaults(_op=target)
    for o in REGISTRY.values():
        if o.group in SHORTCUTS:  # single-op groups (doctor, guide) are only exposed as shortcuts
            continue
        if o.group not in groups:
            gp = sub.add_parser(o.group.replace("_", "-"), help=f"{o.group} commands")
            groups[o.group] = gp.add_subparsers(dest="action", metavar="<action>")
        sp = groups[o.group].add_parser(o.action, help=o.doc.splitlines()[0] if o.doc else "", description=o.doc,
                                        formatter_class=argparse.RawDescriptionHelpFormatter)
        _add_op_args(sp, o)
        if o.name == "analyze.report":
            sp.add_argument("--md", action="store_true")
        sp.set_defaults(_op=o.name)
    return ap


def _collect(o: Op, ns: argparse.Namespace) -> dict[str, Any]:
    hints = typing.get_type_hints(o.fn)
    kwargs = {}
    for name in inspect.signature(o.fn).parameters:
        if not hasattr(ns, name):
            continue
        v = getattr(ns, name)
        if getattr(ns, f"_opt_{name}", None) is not None:
            v = getattr(ns, f"_opt_{name}")
        _, is_list = _base(hints.get(name, str))
        if is_list and v == []:
            v = None
        if v is None and inspect.signature(o.fn).parameters[name].default is inspect.Parameter.empty:
            raise AsamError("MISSING_ARGUMENT", f"'{name}' is required.",
                            fix=f"asam {o.group.replace('_', '-')} {o.action} <{name.upper()}>  (see --help)")
        if is_list and v is not None:
            flat: list[str] = []
            for item in v if isinstance(v, list) else [v]:
                flat += [item] if name in NO_SPLIT else [x.strip() for x in str(item).split(",") if x.strip()]
            v = flat
        kwargs[name] = v
    return kwargs


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `asam auth login` at an interactive terminal -> browser flow; from an agent (no TTY) -> 2-step URL flow
    if argv[:2] == ["auth", "login"] and sys.stdin.isatty() and sys.stdout.isatty() and "--no-wait" not in argv:
        argv[1] = "browser"
    compact = "--compact" in argv
    argv = [a for a in argv if a not in ("--no-wait", "--compact")]
    ap = build_parser()
    ns = ap.parse_args(argv)
    ns.compact = compact
    if ns.group == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0
    if not getattr(ns, "_op", None):
        if ns.group:
            ap.parse_args([ns.group, "--help"])
        ap.print_help()
        return 0
    o = REGISTRY[ns._op]
    try:
        result = o.fn(**_collect(o, ns))
    except AsamError as e:
        print(json.dumps({"error": e.to_dict()}, indent=None if ns.compact else 2, default=str))
        print(f"asam: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    if getattr(ns, "md", False) and isinstance(result, dict) and (result.get("files") or {}).get("markdown"):
        print(Path(result["files"]["markdown"]).read_text())
        return 0
    try:
        print(json.dumps(result, indent=None if ns.compact else 2, default=str, ensure_ascii=False))
    except BrokenPipeError:  # e.g. `asam ... | head`
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
