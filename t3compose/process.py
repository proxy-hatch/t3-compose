"""Small injectable subprocess seam; failures contain public facts only."""
import subprocess
import sys

from .paths import ROOT


class ProcessError(Exception):
    """No command, environment or captured output is stored on this error."""
    def __init__(self, kind, returncode=None):
        if kind not in {"invalid-command", "exit", "not-found", "timeout",
                        "os-error", "invalid-options"}:
            raise ValueError("invalid process failure kind")
        if returncode is not None and (isinstance(returncode, bool) or
                                       not isinstance(returncode, int)):
            raise ValueError("invalid process return code")
        self.kind = kind
        self.returncode = returncode
        message = "process failed: " + kind
        if returncode is not None:
            message += " (return code {})".format(returncode)
        super().__init__(message)


def run(argv, **kwargs):
    """Run a nonempty argument list without a shell; sanitize launch failures."""
    if not isinstance(argv, list) or not argv or kwargs.get("shell", False):
        raise ProcessError("invalid-command")
    try:
        return subprocess.run(argv, **kwargs)
    except subprocess.CalledProcessError as error:
        raise ProcessError("exit", error.returncode) from None
    except FileNotFoundError:
        raise ProcessError("not-found") from None
    except subprocess.TimeoutExpired:
        raise ProcessError("timeout") from None
    except OSError:
        raise ProcessError("os-error") from None
    except (TypeError, ValueError):
        raise ProcessError("invalid-options") from None


def helper_command(*args):
    """Construct the isolated invocation; launching remains the caller's choice."""
    return [sys.executable, "-I", "-S", "-B", str(ROOT / "bin/t3c"), *args]
