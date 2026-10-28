"""Command registry. Modules expose register(registry) and share group(name).

Leaf parsers set handler(args). A handler returns JSON-compatible data,
Outcome(data, code) or PassThrough(status) (for publish run only), or raises
CommandError with a public diagnostic and a listed nonzero ExitCode.
Global --text and --local-dir options precede the command.
"""
import argparse
from enum import IntEnum
import importlib
import importlib.util
import platform
import sys

from . import process
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


def _validate_exit_code(code, allow_zero=True):
    if isinstance(code, bool) or not isinstance(code, int):
        raise ValueError("invalid exit code")
    resolved = ExitCode(code)
    if not allow_zero and resolved == ExitCode.SUCCESS:
        raise ValueError("CommandError code must be nonzero")
    return resolved


class Outcome:
    """Structured output paired with a listed ExitCode."""
    def __init__(self, data, code=ExitCode.SUCCESS):
        self.data = data
        self.code = _validate_exit_code(code, allow_zero=True)


class PassThrough:
    """Child exit status after streaming output; valid only for ('publish', 'run')."""
    def __init__(self, status):
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError("invalid passthrough status")
        if -127 <= status < 0:
            status = 128 + (-status)
        elif not 0 <= status <= 255:
            raise ValueError("invalid passthrough status")
        self.status = status


class CommandError(Exception):
    """The message must be public text, never raw input or a backend exception."""
    def __init__(self, message, code=ExitCode.INVALID_INPUT):
        super().__init__(message)
        self.code = _validate_exit_code(code, allow_zero=False)


class _TextFlagAction(argparse.Action):
    def __init__(self, option_strings, dest, root_parser=None, **kwargs):
        self.root_parser = root_parser
        super().__init__(option_strings, dest, nargs=0, default=False, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, True)
        if self.root_parser is not None:
            self.root_parser.set_text_output(True)


class _SubparsersAction(argparse._SubParsersAction):
    def add_parser(self, name, **kwargs):
        if name in self._name_parser_map:
            raise ValueError("duplicate command or group: " + name)
        return super().add_parser(name, **kwargs)


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, text_output=False, **kwargs):
        self.text_output = bool(text_output)
        self._subparser_actions = []
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)
        self.register("action", "parsers", _SubparsersAction)

    def set_text_output(self, enabled):
        self.text_output = bool(enabled)
        for action in self._subparser_actions:
            for subparser in action._name_parser_map.values():
                if isinstance(subparser, Parser):
                    subparser.set_text_output(enabled)

    def add_subparsers(self, **kwargs):
        kwargs.setdefault("parser_class", lambda **kw: Parser(text_output=self.text_output, **kw))
        action = super().add_subparsers(**kwargs)
        self._subparser_actions.append(action)
        return action

    def print_help(self, file=None):
        help_text = self.format_help()
        print(help_text if self.text_output else
              canonical_json({"help": help_text}).decode("utf-8"), file=file)

    def error(self, message):
        # argparse's default includes supplied values, which may be private.
        raise CommandError("invalid arguments for " + self.prog + "; use --help")


def _is_reserved_name(name):
    return isinstance(name, str) and (name == "local_dir" or name.startswith("t3c_"))


def _validate_parser_names(parser, allowed_dests, allowed_defaults):
    for action in parser._actions:
        if _is_reserved_name(action.dest) and action.dest not in allowed_dests:
            raise ValueError("reserved argument destination: " + action.dest)
    for key in parser._defaults:
        if _is_reserved_name(key) and key not in allowed_defaults:
            raise ValueError("reserved default key: " + key)


class Registry:
    def __init__(self, parser):
        self.parser = parser
        self.commands = parser.add_subparsers(dest="t3c_command", required=True)
        self.groups = {}
        self._group_parsers = {}

    def group(self, name):
        """Return shared subparsers; leaf parsers set_defaults(handler=callable)."""
        if name not in self.groups:
            group_parser = self.commands.add_parser(name)
            selector = "t3c_" + name + "_command"
            self._group_parsers[name] = (group_parser, selector)
            self.groups[name] = group_parser.add_subparsers(dest=selector, required=True)
        return self.groups[name]

    def validate(self):
        _validate_parser_names(
            self.parser,
            allowed_dests={"t3c_text", "t3c_local_dir", "t3c_command"},
            allowed_defaults={"t3c_text", "t3c_local_dir"},
        )
        paths = []
        for root_name, subparser in self.commands._name_parser_map.items():
            if root_name in self._group_parsers:
                group_parser, selector = self._group_parsers[root_name]
                if subparser is not group_parser:
                    raise ValueError("command collides with group: " + root_name)
                if "handler" in group_parser._defaults:
                    raise ValueError("group parser cannot define handler: " + root_name)
                _validate_parser_names(
                    group_parser,
                    allowed_dests={selector},
                    allowed_defaults={"t3c_path"},
                )
                subparsers_action = self.groups[root_name]
                if not subparsers_action._name_parser_map:
                    raise ValueError("empty command group: " + root_name)
                for leaf_name, leaf_parser in subparsers_action._name_parser_map.items():
                    if leaf_parser._subparser_actions:
                        raise ValueError("nested command groups are not supported: " + leaf_name)
                    _validate_parser_names(
                        leaf_parser,
                        allowed_dests=set(),
                        allowed_defaults={"t3c_path"},
                    )
                    handler = leaf_parser.get_default("handler")
                    if not callable(handler):
                        raise ValueError("missing or noncallable handler for " +
                                         root_name + " " + leaf_name)
                    path = (root_name, leaf_name)
                    leaf_parser.set_defaults(t3c_path=path)
                    paths.append(path)
            else:
                if subparser._subparser_actions:
                    raise ValueError("untracked subparsers on command: " + root_name)
                _validate_parser_names(
                    subparser,
                    allowed_dests=set(),
                    allowed_defaults={"t3c_path"},
                )
                handler = subparser.get_default("handler")
                if not callable(handler):
                    raise ValueError("missing or noncallable handler for " + root_name)
                path = (root_name,)
                subparser.set_defaults(t3c_path=path)
                paths.append(path)
        return tuple(sorted(paths))

    def load(self):
        for name in MODULES:
            qualified = "t3compose." + name
            try:
                if importlib.util.find_spec(qualified) is None:
                    continue
                importlib.import_module(qualified).register(self)
                self.validate()
            except (Exception, SystemExit):
                raise CommandError("cannot load command module: " + name) from None
        return self.validate()


def helper_info():
    def git(*arguments):
        return process.run(["git", "-C", str(ROOT), *arguments], check=True,
                           capture_output=True, text=True).stdout.strip()
    return {
        "root": str(ROOT),
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("--no-optional-locks", "status", "--porcelain")),
        "python": platform.python_version(),
    }


def version(args):
    return helper_info()


def _build_parser_and_registry(text_output=False):
    parser = Parser(prog="t3c", text_output=text_output)
    parser.add_argument("--text", dest="t3c_text", action=_TextFlagAction, root_parser=parser)
    parser.add_argument("--local-dir", dest="t3c_local_dir", type=local_dir, default=local_dir())
    registry = Registry(parser)
    registry.commands.add_parser("version").set_defaults(handler=version)
    paths = registry.load()
    return parser, paths


def build_parser(text_output=False):
    parser, _ = _build_parser_and_registry(text_output=text_output)
    return parser


def command_names():
    _, paths = _build_parser_and_registry()
    return paths


def _format_output(data, text_mode):
    encoded = canonical_json(data).decode("utf-8")
    if not text_mode or not isinstance(data, dict):
        return encoded
    return "\n".join("{}: {}".format(key, canonical_json(value).decode("utf-8"))
                     for key, value in data.items())


def _command_path(args):
    if args is None:
        return ()
    path = getattr(args, "t3c_path", None)
    if isinstance(path, tuple):
        return path
    return ()


def main(argv=None):
    path = ()
    try:
        argv = list(sys.argv[1:] if argv is None else argv)
        parser = build_parser()
        args = parser.parse_args(argv)
        path = _command_path(args)
        args.local_dir = args.t3c_local_dir
        result = args.handler(args)
        if isinstance(result, PassThrough):
            if path != ("publish", "run"):
                raise ValueError("PassThrough is restricted to publish run")
            return result.status
        if isinstance(result, Outcome):
            data, code = result.data, result.code
        else:
            data, code = result, ExitCode.SUCCESS
        print(_format_output(data, args.t3c_text))
        return code
    except CommandError as error:
        print(str(error), file=sys.stderr)
        return error.code
    except Exception as error:
        prog = " ".join(("t3c", *path))
        print("{}: command failed ({}); check local inputs and helper installation".format(
            prog, type(error).__name__), file=sys.stderr)
        return ExitCode.INVALID_INPUT
