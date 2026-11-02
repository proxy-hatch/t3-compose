"""Synthetic skill installation: the link, its status and the registration contract."""
import contextlib
import importlib
import io
import json
import os
import re
from unittest.mock import patch

from t3compose import cli, process, skill_install
from tests.support import OfflineTestCase, ROOT, guarded_helper_command

NAME = "t3-compose-manager"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        actual = cli.main(list(argv))
    return actual, out.getvalue(), err.getvalue()


class OfflineSkillInstall(OfflineTestCase):
    def setUp(self):
        self.skills = self.temp / "skills"
        self.skills.mkdir()
        self.source = self.temp / "checkout" / "skill"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: synthetic\n---\n")
        self.enterContext(patch.object(skill_install, "skill_dir", lambda: self.source))
        self.link = self.skills / NAME

    def invoke(self, leaf, *options, code=0, skills=None):
        argv = ["skill", leaf, "--skills-dir", str(skills or self.skills), *options]
        actual, out, err = run_cli(*argv)
        self.assertEqual(actual, code, (out, err))
        if err:  # a refusal carries public text only, and no data
            self.assertEqual(out, "")
            self.assertNotIn(str(self.temp), err)
        return json.loads(out) if out else None

    def test_install_links_the_skill_and_status_agrees(self):
        data = self.invoke("install")
        self.assertEqual((data["state"], data["changed"], data["name"]), ("installed", True, NAME))
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(os.readlink(self.link), str(self.source))
        self.assertEqual((data["target"], data["expectedTarget"]), (str(self.source),) * 2)
        self.assertTrue(data["skillFile"])
        status = self.invoke("status")
        self.assertEqual(status["state"], "installed")
        self.assertNotIn("changed", status)

    def test_install_is_idempotent_for_the_same_link(self):
        self.invoke("install")
        before = os.lstat(self.link)
        data = self.invoke("install")
        self.assertEqual((data["state"], data["changed"]), ("installed", False))
        self.assertEqual(os.lstat(self.link).st_ino, before.st_ino)

    def test_install_refuses_a_name_in_use_and_changes_nothing(self):
        cases = {}
        for name in ("a-file", "a-dir", "foreign", "broken"):
            cases[name] = self.skills / name
        cases["a-file"].write_text("keep")
        cases["a-dir"].mkdir()
        elsewhere = self.temp / "elsewhere"
        elsewhere.mkdir()
        cases["foreign"].symlink_to(elsewhere)
        cases["broken"].symlink_to(self.temp / "gone")
        for name, path in cases.items():
            before = (path.is_symlink(), os.readlink(path) if path.is_symlink() else None,
                      path.read_text() if path.is_file() and not path.is_symlink() else None)
            self.invoke("install", "--name", name, code=1)
            after = (path.is_symlink(), os.readlink(path) if path.is_symlink() else None,
                     path.read_text() if path.is_file() and not path.is_symlink() else None)
            self.assertEqual(before, after, name)
        self.assertEqual(os.readlink(cases["foreign"]), str(elsewhere))
        self.assertTrue(cases["a-dir"].is_dir())

    def test_a_link_made_between_the_check_and_the_write_is_a_refusal(self):
        with patch.object(skill_install.os, "symlink", side_effect=FileExistsError):
            self.invoke("install", code=1)
        with patch.object(skill_install.os, "symlink", side_effect=PermissionError):
            self.invoke("install", code=2)
        self.assertEqual(list(self.skills.iterdir()), [])

    def test_status_reports_every_state_with_data_on_failure(self):
        missing = self.invoke("status", code=1)
        self.assertEqual((missing["state"], missing["target"], missing["skillFile"]),
                         ("missing", None, False))
        self.link.write_text("not a link")
        self.assertEqual(self.invoke("status", code=1)["state"], "not-a-link")
        self.link.unlink()
        elsewhere = self.temp / "elsewhere"
        elsewhere.mkdir()
        self.link.symlink_to(elsewhere)
        foreign = self.invoke("status", code=1)
        self.assertEqual((foreign["state"], foreign["target"]), ("foreign-link", str(elsewhere)))
        self.link.unlink()
        self.link.symlink_to(self.temp / "gone")
        self.assertEqual(self.invoke("status", code=1)["state"], "broken-link")
        self.link.unlink()
        self.link.symlink_to(self.source)
        self.assertEqual(self.invoke("status")["state"], "installed")

    def test_a_link_to_a_skill_without_its_file_is_not_a_good_install(self):
        self.link.symlink_to(self.source)
        (self.source / "SKILL.md").unlink()
        data = self.invoke("status", code=1)
        self.assertEqual((data["state"], data["skillFile"]), ("installed", False))

    def test_custom_name_leaves_the_default_alone(self):
        self.invoke("install", "--name", "other-name")
        self.assertTrue((self.skills / "other-name").is_symlink())
        self.assertFalse(self.link.exists() or self.link.is_symlink())

    def test_invalid_names_create_nothing(self):
        for name in ("", "Bad", "-x", "a/b", "..", "a b", "a_b", "x" * 65):
            self.invoke("install", "--name=" + name, code=2)
        self.assertEqual(list(self.skills.iterdir()), [])

    def test_skills_directory_must_exist_and_stay_outside_the_skill(self):
        self.invoke("install", code=2, skills=self.temp / "missing")
        plain = self.temp / "plain-file"
        plain.write_text("x")
        self.invoke("install", code=2, skills=plain)
        self.invoke("status", code=2, skills=self.temp / "missing")
        nested = self.source / "nested"
        nested.mkdir()
        self.invoke("install", code=2, skills=nested)
        self.invoke("install", code=2, skills=self.source)
        self.assertEqual(list(nested.iterdir()), [])

    def test_an_alias_of_the_skill_or_of_a_directory_in_it_is_refused(self):
        inside = self.source / "nested"
        inside.mkdir()
        alias, deep, reached = (self.temp / name for name in ("alias", "deep-alias", "source-alias"))
        alias.symlink_to(self.source)
        deep.symlink_to(inside)
        reached.symlink_to(self.source)
        for directory in (alias, deep):
            for leaf in ("install", "status"):
                self.invoke(leaf, code=2, skills=directory)
        with patch.object(skill_install, "skill_dir", lambda: reached):  # the skill, via a link
            self.invoke("install", code=2, skills=inside)
        self.assertEqual(sorted(path.name for path in self.source.iterdir()), ["SKILL.md", "nested"])
        self.assertEqual(list(inside.iterdir()), [])

    def test_incomplete_source_is_refused_before_any_write(self):
        (self.source / "SKILL.md").unlink()
        self.invoke("install", code=2)
        self.assertEqual(list(self.skills.iterdir()), [])

    def test_commands_are_registered(self):
        names = cli.command_names()
        self.assertIn(("skill", "install"), names)
        self.assertIn(("skill", "status"), names)

    def test_real_entry_point_installs_and_reports_the_real_skill(self):
        local = self.temp / "state"
        base = ["--local-dir", str(local), "skill"]
        def child(leaf):
            return process.run(guarded_helper_command(*base, leaf, "--skills-dir", str(self.skills)),
                               capture_output=True, text=True)
        installed = child("install")
        self.assertEqual((installed.returncode, installed.stderr), (0, ""), installed.stdout)
        link = self.skills / NAME
        self.assertEqual(os.path.realpath(link), os.path.realpath(ROOT / "skill"))
        self.assertTrue((link / "SKILL.md").is_file())
        status = child("status")
        self.assertEqual((status.returncode, json.loads(status.stdout)["state"]), (0, "installed"))


class OfflineSkillRegistration(OfflineTestCase):
    def test_import_and_registration_do_no_io(self):
        with patch.object(process, "run", side_effect=AssertionError("unexpected process")), \
                patch("builtins.open", side_effect=AssertionError("unexpected read")), \
                patch("os.open", side_effect=AssertionError("unexpected open")), \
                patch("os.stat", side_effect=AssertionError("unexpected stat")), \
                patch("os.lstat", side_effect=AssertionError("unexpected stat")), \
                patch("os.readlink", side_effect=AssertionError("unexpected read")), \
                patch("os.symlink", side_effect=AssertionError("unexpected write")):
            importlib.reload(skill_install)
            registry = cli.Registry(cli.Parser(prog="synthetic"))
            skill_install.register(registry)
            registry.validate()


class OfflineSkillInstallDocs(OfflineTestCase):
    def test_front_matter_names_the_skill_the_installer_links(self):
        text = (ROOT / "skill/SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(re.match(r"---\nname: (\S+)\n", text).group(1), skill_install.DEFAULT_NAME)

    def test_readme_spec_and_skill_name_the_install_commands(self):
        skill = " ".join((ROOT / "skill/SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("`t3c skill install --skills-dir DIR` links this directory into a skills directory",
                      skill)
        for path in ("README.md", "docs/cli/skill_install.md"):
            text = (ROOT / path).read_text(encoding="utf-8")
            for command in ("t3c skill install", "t3c skill status"):
                self.assertIn(command, text, path)
