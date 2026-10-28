"""Command registry. Modules expose register(registry) and share group(name).

Leaf parsers set handler(args). A handler returns JSON-compatible data on
success or raises CommandError with a public diagnostic and an ExitCode.
Global --text and --local-dir options precede the command.
"""
import argparse
from enum import IntEnum
from functools import partial
import importlib
import importlib.util
import platform
import subprocess
import sys

from .jsonio import canonical_json
from .paths import ROOT, local_dir

MODULES = ("config", "records", "evidence", "jev", "routing", "quota", "privacy",
           "pullrequest", "github_app", "token_run", "publish", "heavy", "briefs",
           "skill_install")


class ExitCode(IntEnum):
    SUCCESS = 0
    CHECK_FAILED = 1
    INVALID_INPUT = 2
    INVALID_CONFIG = 3
    JUDGMENT_NEEDED = 4
    REPORT = 5
    BUSY = 75


class CommandError(Exception):
    """The message must be public text, never raw input or a backend exception."""
    def __init__(self, message, code=ExitCode.INVALID_INPUT):
        super().__init__(message)
        self.code = ExitCode(code)


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, text_output=False, **kwargs):
        self.text_output = text_output
        super().__init__(*args, **kwargs)

    def add_subparsers(self, **kwargs):
        return super().add_subparsers(
            parser_class=partial(Parser, text_output=self.text_output), **kwargs)

    def print_help(self, file=None):
        help_text = self.format_help()
        print(help_text if self.text_output else
              canonical_json({"help": help_text}).decode("utf-8"), file=file)

    def error(self, message):
        # argparse's default includes supplied values, which may be private.
        raise CommandError("invalid arguments; use --help")


class Registry:
    def __init__(self, parser):
        self.commands = parser.add_subparsers(dest="command", required=True)
        self.groups = {}

    def group(self, name):
        """Return shared subparsers; leaf parsers set_defaults(handler=callable)."""
        if name not in self.groups:
            parser = self.commands.add_parser(name)
            self.groups[name] = parser.add_subparsers(dest=name + "_command", required=True)
        return self.groups[name]

    def load(self):
        for name in MODULES:
            qualified = "t3compose." + name
            try:
                if importlib.util.find_spec(qualified) is None:
                    continue
                importlib.import_module(qualified).register(self)
            except Exception:
                raise CommandError("cannot load command module: " + name) from None


def version(args):
    def git(*arguments):
        return subprocess.run(["git", "-C", str(ROOT), *arguments], check=True,
                              capture_output=True, text=True).stdout.strip()
    return {"root": str(ROOT), "commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")), "python": platform.python_version()}


def main(argv=None):
    try:
        argv = list(sys.argv[1:] if argv is None else argv)
        parser = Parser(prog="t3c", text_output="--text" in argv)
        parser.add_argument("--text", action="store_true")
        parser.add_argument("--local-dir", type=local_dir, default=local_dir())
        registry = Registry(parser)
        registry.commands.add_parser("version").set_defaults(handler=version)
        registry.load()
        args = parser.parse_args(argv)
        data = args.handler(args)
        encoded = canonical_json(data).decode("utf-8")
        if args.text:
            encoded = ("\n".join("{}: {}".format(key, value) for key, value in data.items())
                       if isinstance(data, dict) else str(data))
        print(encoded)
        return ExitCode.SUCCESS
    except CommandError as error:
        print(str(error), file=sys.stderr)
        return error.code
    except Exception:
        print("command failed; check local inputs and helper installation", file=sys.stderr)
        return ExitCode.INVALID_INPUT
