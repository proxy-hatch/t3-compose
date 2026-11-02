"""The SKILL.md acceptance contract: rules, command coverage and cross-document anchors."""
import argparse
import re

from t3compose import cli
from tests.support import OfflineTestCase, ROOT

SKILL = ROOT / "skill/SKILL.md"
FENCE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)
SUITE_COMMAND = ('"$PY" -I -S -c \'import fcntl,subprocess,sys; f=open("/tmp/t3c-suite.lock","a"); '
                 'fcntl.flock(f,fcntl.LOCK_EX); sys.exit(subprocess.call(sys.argv[1:],timeout=1500))\' '
                 '"$PY" -I -S -B -m unittest discover -s tests -t .')

# The lead's steps after a quota refusal, which the skill and the matrix document both state.
REFUSAL_STEPS = (
    "Record the refusal with `t3c quota record`.",
    "Clear the refused dispatch with a stopped-and-preserved `reconciliation`.",
    "Start at the round's previous start commit, or at the latest completed result's commit.",
    "The refused child's commits, if any, stay on its branch. The new brief may name that branch "
    "for read-only reference.",
)

EPOCH_RULE = "Never run `route input` and then `route classify` for the same task and policy:"

# Each rule the skill must state, as a clause of the section that owns it.
CLAUSES = {
    "Invocation": (
        "uv python pin 3.15 --global", 'PY="$(uv python find 3.15)"',
        '`t3c` below abbreviates `"$PY" -I -S <root>/bin/t3c`',
        "Never take evidence on a pre-release interpreter.",
        '`"$PY" -I -S -B -m unittest discover -s tests -t . -v`',
        "print their report with exit 1 or 3",
        "4 manager judgment needed, 5 report to Shawn, 75 busy",
        "Shared core files", "change only when a brief assigns the edit",
    ),
    "Hard rules": (
        "The manager makes no project edits.",
        "Workers commit on their branch only. Workers and Reviewers never push, open, edit or "
        "merge pull requests, and never post to GitHub.",
        "squash-merges it (plan decision 19). The manager does not merge, and no lead merges "
        "another Task's pull request.",
        "Never read, print or pass a secret in argv. A Keychain prompt gets Allow, never Always Allow.",
    ),
    "Roles": (
        "A Feature lead runs one Task", "It never reports readiness or progress to the manager.",
        "or a stage-3 cell with no target that can serve",
        "It does not report ordinary usage-limit refusals, agy's included: it records them with "
        "`t3c quota record` and takes the cell's next target.",
        "With no lead in a managed repository, the manager owns the Task's coordination",
    ),
    "Start and restart": (
        "Confirm the thread is bound to the repository it coordinates.",
        "Start nothing new until every item is reconciled.",
        "`/clear`, sent with `t3_thread_send`, clears a Claude session's conversation and keeps "
        "the thread",
        "A provider switch with `t3_thread_configure` does not clear anything",
        "A repeated `delegate_task` with the same `clientRequestId` returns the existing task and "
        "starts no second child.",
    ),
    "Records from the first dispatch": (
        "Every Task runs in t3c records from its first dispatch.",
        "A work's review base is immutable: a Task that moves onto a newer `main` opens a new "
        "work epoch (`<ID>-main-<short sha>`).",
        "A Task with an assigned Feature lead starts with the manager's `lead-assignment` as its "
        "first event",
        "A Task without a lead (a managed repository before Feature leads are enabled) has no "
        "`lead-assignment`: the records hold no lead until one is assigned, so its first event is "
        "the manager's own. The manager coordinates it, and an independent Reviewer reviews its "
        "final head.",
        "`dispatch-intent` before the call, `dispatch-confirmed` with the `taskId`, then the "
        "genuine `result`.",
        "Never backdate or rewrite an event.",
    ),
    "Routing": (
        "A standard Task starts at stage 1 and a complex Task at stage 2",
        "Start an escalation epoch, which is one issue or round, in exactly one of two ways.",
        "`t3c route classify --repo REPO --work ID --input TASK.json` alone. It records its own "
        "input and Jev's decision, and returns the `inputRef` and `policyDigest`.",
        EPOCH_RULE + " classify then exits 2 as an unclassified input and appends no second input.",
        "Use the epoch's `inputRef` and `policyDigest` for every later `route select` and "
        "`route escalate`.",
        "capture it once, save the exact tool output to a file with its true UTC capture time, "
        "and reuse that file for the Task",
        "Capture again only after a dispatch error that suggests a provider change",
        "the catalog is at most 24 hours old",
        "Escalations recorded after that input, with the same role and lane, chained stage by "
        "stage from the starting stage, allow a higher `--stage`",
        "Always dispatch the cell's primary. Never estimate or pre-check quota.",
        "Quota failover is horizontal.",
        "gets one retry on the same target after the stated wait, or after one minute when none "
        "is stated",
        "A recorded refusal keeps its provider instance or pool account and model out of use for "
        "30 minutes.",
        "A dry cell never escalates to another stage. At stage 3 the item waits and the lead tells "
        "the manager, who reports to Shawn.",
        "Classify a failed child by its own response, and use the harness's label only when the "
        "child gave none.",
        "GLM (metered, per token) is a last-resort quota fallback only. It is never a primary, a "
        "lead, a pre-pass or a helper, and its thinking is low or high, never max.",
        "Record `billing: per-token` for each use and list every GLM use in the merge line.",
        "about $20 a month",
        "advisory, no fallback, skipped when unavailable, never the review of record",
        "When none is configured or eligible, the command exits 0 with `{skipped: true, reason, "
        "retryAfter, targetsTried}` and writes nothing; go on to the review of record.",
        "`route select --role verifier|text` needs a current input but no decision",
        "it reports `startingStage` for a stage below the start and `reachedStage` for a stage "
        "above the recorded escalation chain",
    ),
    "Dispatch": (
        "Record `dispatch-intent` with `--checkout PATH` (the dispatching checkout: the store "
        "checks that its HEAD equals the provisioned commit and that the start commit exists)",
        "the instance, model and options `route select` returned as its `selection`",
        'call `delegate_task` with `isolation: "worktree"`, `mode: "async"`, that same `target`',
        "`t3c-<work>-<role>-r<round>`",
        "Its exact `Move-rule: three-case-v1` block",
        "to move its own branch to the start commit only when HEAD equals the provisioned commit, "
        "the tree is clean, the start commit exists and the branch is its fresh own branch",
        "never fetching a missing start commit",
        "The digest they print is the intent's `briefDigest`.",
        "Cancel a child that landed wrong within seconds with `task_cancel`.",
    ),
    "Escalation": (
        "A Worker stops after two failed repairs",
        "returns only an `<ESCALATION_REQUEST>` block",
        "sets the start commit to the WIP commit",
        "the round's first Worker brief, never a continuation brief",
        "The step-by-step form of these rules is `<root>/skill/templates/lead-protocol.md`.",
        "`t3c brief render --template lead-protocol --values VALUES.json --out FILE`",
        "It never passes the failed Worker's transcript.",
        "checks the returned block with `t3c brief check-escalation --result FILE`",
        "(`worker-hard-stop`); a finding still open after two Corrections rounds "
        "(`finding-unresolved`, a Worker); a review deadlock (`review-deadlock`, a Reviewer; a "
        "next-stage reviewer decides); two non-quota failures of a reviewer (`reviewer-failure`, "
        "a Reviewer); a stalled lead (`lead-stalled`, a Feature lead",
        "Quota exhaustion never authorizes escalation.",
        "Record a trigger before selecting the next stage: `t3c route escalate --repo REPO --work "
        "ID --input-ref SEQ --policy-digest DIGEST --role worker|reviewer|feature-lead "
        "--from-stage 1|2 --reason REASON --evidence-file FILE [--wip-commit SHA]`",
        "`--from-stage` must be the stage the epoch has reached; the command derives the next "
        "stage, and there is no stage 4.",
        *REFUSAL_STEPS,
    ),
    "Reviews and the final head": (
        "The review of record never sees the pre-pass findings, and `t3c brief check` refuses a "
        "review-of-record brief that carries a `PP-<n>` ID.",
        "That review's brief has Round `final-review`.",
        "A task `review` names the confirmed native Reviewer task; a lead `review` names the "
        "assigned lead's thread, never an invented task ID.",
        "On a core Task the review of record reviews the exact final head, and the lead then "
        "checks its result and records its own review last.",
        "On a non-critical Task the lead's own review stands.",
        "Evidence order for a core Task, at the exact final head: the required checks and a "
        "`criterion-evidence` event for each acceptance criterion; the independent `review` event "
        "(reviewer kind `task`); then the lead's `review` event (kind `lead`) at the same base "
        "and head.",
        'must print `"verified":true` with no gaps before anything is published.',
    ),
    "Required checks": (
        "Every full SUITE run goes through the machine-wide lock, with this command: `"
        + SUITE_COMMAND + "`.",
        "A Worker runs it once before reporting; the lead once on the final head; the manager "
        "once after the merge.",
        "Reviewers and docs-only Workers run no full SUITE.",
        "runs through `t3c heavy run`, one at a time on the whole machine",
        "Keep the completion summary: `label`, `commit`, `log`, `childStatus`, `headUnchanged`, "
        "`treeUnchanged`, `startedAt` and `endedAt`. A pass needs `childStatus` 0 and both "
        "freshness flags true.",
    ),
    "Publication and merge": (
        "Link the returned URL at once with `link_pull_request`.",
        "`t3c publish run --status-file FILE -- gh pr merge N --repo OWNER/NAME --squash "
        "--match-head-commit SHA`",
        "Supply no title, body, author email or admin bypass",
        "Send the manager one line:",
        "Never force a push, weaken a lease, duplicate a pull request, or use personal GitHub "
        "credentials.",
    ),
    "Updates and installation": (
        "Installing writes outside `.local/`, so it waits for Shawn's explicit request.",
    ),
}


def invocations(text):
    """Every `t3c ...` command in fenced blocks and code spans, however a span wraps."""
    found = [" ".join(line.split()) for block in FENCE.findall(text)
             for line in block.splitlines() if line.strip().startswith("t3c ")]
    for span in re.findall(r"`([^`]+)`", FENCE.sub("", text)):
        command = " ".join(span.split())
        if command.startswith("t3c "):
            found.append(command)
    return found


def resolve(line, registered):
    """The registered command path a line names; None for a placeholder."""
    words = line.split()[1:]
    while words and words[0].startswith("-"):
        words = words[2:] if words[0] == "--local-dir" else words[1:]
    if not words or not words[0][:1].islower():
        return None
    root, rest = words[0], words[1:]
    if (root,) in registered:  # a command without a group takes options only
        return (root,) if all(word.startswith("-") for word in rest) else ("unknown", *words[:2])
    if rest and (root, rest[0]) in registered:
        return root, rest[0]
    return ("unknown", *words[:2])


def required_options(parser):
    """Every registered command path -> the options its parser requires."""
    found = {}

    def walk(node, path):
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    walk(child, path + (name,))
        found[path] = {a.option_strings[0] for a in node._actions if a.required and a.option_strings}
    walk(parser, ())
    return found


def sections(text):
    """The raw body of each `##` section, by heading."""
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


class OfflineSkillDocument(OfflineTestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.registered = set(cli.command_names())
        self.named = [resolve(line, self.registered) for line in invocations(self.text)]

    def test_front_matter_names_the_skill(self):
        match = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", self.text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "t3-compose-manager")
        self.assertGreater(len(match.group(2)), 40)

    def test_the_command_scan_reads_wrapped_spans_and_checks_group_and_leaf(self):
        registered = {("version",), ("work", "list"), ("work", "show")}
        text = ("Run `t3c work\n  list --repo R` and `t3c version`.\n\n```sh\nt3c work show --id W\n```\n"
                "A wrapped `t3c work\n  bogus --repo R`, `t3c version now`, `t3c work`, `t3c <command>`"
                " and `t3c --local-dir D --text version`.\n")
        self.assertEqual([resolve(line, registered) for line in invocations(text)],
                         [("work", "show"), ("work", "list"), ("version",),
                          ("unknown", "work", "bogus"), ("unknown", "version", "now"),
                          ("unknown", "work"), None, ("version",)])

    def test_every_command_the_skill_names_exists(self):
        self.assertEqual(self.text.count("`") % 2, 0)
        self.assertEqual([path for path in self.named if path and path[0] == "unknown"], [])

    def test_an_option_template_names_every_option_its_command_requires(self):
        required = required_options(cli.build_parser())
        short = []
        for line in invocations(self.text):
            path = resolve(line, self.registered)
            given = set(re.findall(r"--[a-z][a-z-]*", line))
            if path and path[0] != "unknown" and given and "..." not in line:  # "..." marks an elided template
                missing = required[path] - given
                if missing:
                    short.append((" ".join(path), sorted(missing)))
        self.assertEqual(short, [])

    def test_every_registered_command_appears_in_the_skill(self):
        self.assertEqual(sorted(self.registered - set(self.named)), [])
        rows = re.findall(r"^\| `(t3c [^`]+)` \|", sections(self.text)["Command reference"], re.M)
        self.assertEqual(sorted(resolve(row, self.registered) for row in rows),
                         sorted(self.registered))

    def test_the_skill_states_each_rule_in_the_section_that_owns_it(self):
        body = {title: " ".join(text.split()) for title, text in sections(self.text).items()}
        missing = [(title, clause) for title, clauses in CLAUSES.items() for clause in clauses
                   if clause not in body.get(title, "")]
        self.assertEqual(missing, [])

    def test_the_specs_state_what_the_skill_states(self):
        def text(path):
            return " ".join((ROOT / path).read_text(encoding="utf-8").split())
        self.assertEqual([step for step in REFUSAL_STEPS if step not in text("docs/routing-matrix.md")], [])
        plan = text("docs/superpowers/plans/2026-10-27-t3-compose-implementation.md")
        self.assertIn("clear the refused dispatch with a stopped-and-preserved reconciliation", plan)
        self.assertNotIn("continuing at the refused child's last commit", plan)
        self.assertIn("captured once per thread and at most 24 hours old", plan)
        notice = "a stage-3 cell with no target that can serve (never ordinary usage-limit refusals)"
        for path in ("docs/superpowers/specs/2026-10-27-t3-compose-design.md",
                     "docs/superpowers/plans/2026-10-27-t3-compose-implementation.md"):
            self.assertIn(notice, text(path))
        for clause in (EPOCH_RULE, "in exactly one of two ways"):
            self.assertIn(clause, text("docs/cli/routing.md"))
            self.assertIn(clause, text("skill/SKILL.md"))

    def test_the_paths_the_skill_names_exist(self):
        for path in re.findall(r"<root>/([\w./*-]+)", self.text):
            self.assertTrue(list(ROOT.glob(path.rstrip("."))), path)

    def test_the_skill_is_public_and_survives_the_symlink(self):
        for pattern in (r"[\w.+-]+@[\w-]+\.[\w.-]+", r"/Users/", r"/home/", r"\bmcp:", r"\bthread:",
                        r"\bnode:delegated", r"\bsk-[A-Za-z0-9]{8}"):
            self.assertIsNone(re.search(pattern, self.text), pattern)
        # A relative link would break through the user-level link.
        self.assertIsNone(re.search(r"\]\((?!https?://|#)", self.text))

    def test_readme_and_agents_point_at_the_skill(self):
        for name in ("README.md", "AGENTS.md"):
            self.assertIn("skill/SKILL.md", (ROOT / name).read_text(encoding="utf-8"))
