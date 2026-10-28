"""Offline guard; not a security boundary for arbitrary child programs."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp = Path(self.enterContextCompat(tempfile.TemporaryDirectory(dir=ROOT)))
        popen = subprocess.Popen

        def refuse(*args, **kwargs):
            raise AssertionError("offline guard blocked external access")

        def guarded_popen(args, *rest, **kwargs):
            forbidden = {"security", "gh", "ssh", "curl", "sh", "bash", "zsh",
                         "dash", "ksh", "fish"}
            if (kwargs.get("shell") or isinstance(args, (str, bytes)) or
                    any(os.fsdecode(arg).rsplit("/", 1)[-1] in forbidden for arg in args) or
                    (kwargs.get("executable") and
                     Path(kwargs["executable"]).name in forbidden)):
                refuse()
            return popen(args, *rest, **kwargs)

        for name in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
            if hasattr(socket.socket, name):
                self.enterContextCompat(patch.object(socket.socket, name, refuse))
        for name in ("socket", "SocketType", "socketpair", "create_connection", "getaddrinfo",
                     "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
            self.enterContextCompat(patch.object(socket, name, refuse))
        for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open",
                       "os.system", "os.popen"):
            self.enterContextCompat(patch(target, refuse))
        self.enterContextCompat(patch.object(subprocess, "Popen", guarded_popen))

    def enterContextCompat(self, context):
        """TestCase.enterContext for the supported Python 3.9 interpreter."""
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value
