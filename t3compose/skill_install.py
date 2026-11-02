"""Install and inspect the manager skill's user-level link."""
import os
from pathlib import Path
import re
import stat

from . import paths
from .cli import CommandError, ExitCode, Outcome

DEFAULT_NAME = "t3-compose-manager"
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


def skill_dir():
    """The skill directory of this checkout: the link target."""
    return paths.ROOT / "skill"


def _location(args):
    """Validate the arguments; return the link path and the skill directory."""
    if not NAME_PATTERN.fullmatch(args.name):
        raise CommandError("invalid skill name", ExitCode.INVALID_INPUT)
    if not args.skills_dir:
        raise CommandError("skills directory not found", ExitCode.INVALID_INPUT)
    directory = Path(os.path.abspath(args.skills_dir))
    if not directory.is_dir():
        raise CommandError("skills directory not found", ExitCode.INVALID_INPUT)
    source = skill_dir()
    real_directory, real_source = Path(os.path.realpath(directory)), Path(os.path.realpath(source))
    if real_directory == real_source or real_source in real_directory.parents:
        raise CommandError("skills directory cannot be inside the skill", ExitCode.INVALID_INPUT)
    return directory / args.name, source


def _inspect(link, source):
    """The state of the link path, read without following a final symlink."""
    try:
        mode = os.lstat(link).st_mode
        if not stat.S_ISLNK(mode):
            return "not-a-link", None
        target = os.readlink(link)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        raise CommandError("skill link could not be read", ExitCode.INVALID_INPUT) from None
    if not os.path.exists(link):
        return "broken-link", target
    if os.path.realpath(link) == os.path.realpath(source):
        return "installed", target
    return "foreign-link", target


def _report(link, source, state, target, **extra):
    return {"name": link.name, "path": str(link), "state": state, "target": target,
            "expectedTarget": str(source),
            "skillFile": os.path.isfile(os.path.join(link, "SKILL.md")), **extra}


def install(args):
    link, source = _location(args)
    if not (source / "SKILL.md").is_file():
        raise CommandError("skill source is incomplete", ExitCode.INVALID_INPUT)
    state, target = _inspect(link, source)
    if state == "installed":
        return _report(link, source, state, target, changed=False)
    if state != "missing":
        raise CommandError("skill install refused: the name is in use; remove it yourself first",
                           ExitCode.CHECK_FAILED)
    try:
        os.symlink(str(source), str(link))
    except FileExistsError:
        raise CommandError("skill install refused: the name is in use; remove it yourself first",
                           ExitCode.CHECK_FAILED) from None
    except OSError:
        raise CommandError("skill link could not be created", ExitCode.INVALID_INPUT) from None
    return _report(link, source, "installed", str(source), changed=True)


def status(args):
    link, source = _location(args)
    state, target = _inspect(link, source)
    report = _report(link, source, state, target)
    good = state == "installed" and report["skillFile"]
    return Outcome(report, ExitCode.SUCCESS if good else ExitCode.CHECK_FAILED)


def register(registry):
    for leaf, handler in (("install", install), ("status", status)):
        parser = registry.group("skill").add_parser(leaf)
        parser.add_argument("--skills-dir", required=True)
        parser.add_argument("--name", default=DEFAULT_NAME)
        parser.set_defaults(handler=handler)
