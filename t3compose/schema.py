"""Pure record and event schema validation (RC-1a, part 1 of 3)."""
from datetime import date, datetime
import hashlib
import math
import re
from types import MappingProxyType

from .jsonio import MAX_INT_DIGITS, canonical_json

SCHEMA_VERSION = 1
MAX_ROUTING_INPUT_BYTES = 16384
MAX_PAYLOAD_BYTES = 16384
MAX_JSON_DEPTH = 8

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_WORK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FACT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UTC_DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_UTC_TS_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z$")
_DOMAIN_OPTIONS = ("SOFTWARE_ENGINEERING", "OTHER", "MIXED_OR_UNCLEAR")
_DIFFICULTY_OPTIONS = ("EASY", "MEDIUM", "CHALLENGING", "INSUFFICIENT_CONTEXT")
_ROLES = ("worker", "reviewer", "feature-lead", "text-helper")
_SELECTION_ROLES = ("worker", "reviewer", "feature-lead")
_CHECK_SOURCES = ("manager", "feature-lead", "reviewer", "assigned-session", "worker")


class SchemaError(ValueError):
    """Sanitized schema failure naming only the field path and public rule."""
    def __init__(self, path, rule):
        self.path, self.rule = str(path), str(rule)
        super().__init__(f"{self.path}: {self.rule}")


def _fail(path, rule):
    raise SchemaError(path, rule)

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)

def _check_int_digits(value, path):
    if abs(value) >= 10 ** MAX_INT_DIGITS:
        _fail(path, "integer exceeds digit limit")

def _check_text(value, path):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        _fail(path, "expected nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail(path, "invalid UTF-8 string")

def _check_regex(pattern, rule):
    def validator(value, path):
        _check_text(value, path)
        if not pattern.fullmatch(value):
            _fail(path, rule)
    return validator

_check_commit_sha = _check_regex(_COMMIT_RE, "expected 40-character lowercase hex SHA")
_check_digest = _check_regex(_DIGEST_RE, "expected sha256:<64 lowercase hex>")
_check_id_token = _check_regex(_ID_TOKEN_RE, "invalid identifier token")
_check_work_id = _check_regex(_WORK_ID_RE, "invalid work identifier")
_check_fact_id = _check_regex(_FACT_ID_RE, "expected lowercase hyphenated fact ID")

def _check_pos_int(value, path):
    if not _is_int(value) or value < 1:
        _fail(path, "expected positive integer")
    _check_int_digits(value, path)

def _check_nonneg_int(value, path):
    if not _is_int(value) or value < 0:
        _fail(path, "expected nonnegative integer")
    _check_int_digits(value, path)

def _check_bool(value, path):
    if not isinstance(value, bool):
        _fail(path, "expected boolean")

def _check_enum(*allowed):
    allowed_set = frozenset(allowed)
    return lambda val, p: _fail(p, "invalid enum value") if not isinstance(val, str) or val not in allowed_set else None

def _check_utc_date(value, path):
    _check_text(value, path)
    match = _UTC_DATE_RE.fullmatch(value)
    if not match:
        _fail(path, "expected YYYY-MM-DD date")
    try:
        date(*(int(part) for part in match.groups()))
    except ValueError:
        _fail(path, "invalid calendar date")

def _check_utc_timestamp(value, path):
    _check_text(value, path)
    match = _UTC_TS_RE.fullmatch(value)
    if not match:
        _fail(path, "expected ISO 8601 UTC timestamp ending in Z")
    year, month, day, hour, minute, second, micros = match.groups()
    try:
        datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), int((micros or "").ljust(6, "0")))
    except ValueError:
        _fail(path, "invalid UTC timestamp")

def _parse_repo_identity(value, path):
    _check_text(value, path)
    parts = value.split("/")
    if any(not part or part in {".", ".."} or not _SEGMENT_RE.fullmatch(part) for part in parts):
        _fail(path, "invalid repository identity")
    if (len(parts) == 2 and parts[0] == "synthetic") or (
            len(parts) == 3 and parts[0] != "synthetic" and ("." in parts[0] or parts[0] == "localhost")):
        return value
    _fail(path, "expected <host>/<owner>/<name> or synthetic/<name>")

def _check_repo_identity(value, path):
    _parse_repo_identity(value, path)

def _parse_writer_id(value, path):
    _check_text(value, path)
    if "@" not in value:
        _fail(path, "expected writer identity with @<repository>")
    actor, repo = value.split("@", 1)
    _parse_repo_identity(repo, path)
    if actor == "manager":
        return ("manager", None, repo)
    if actor.startswith("lead:"):
        _check_work_id(actor[5:], path)
        return ("lead", actor[5:], repo)
    _fail(path, "expected manager@<identity> or lead:<WORK-ID>@<identity>")

def _check_writer_id(value, path):
    _parse_writer_id(value, path)

def _check_manager_writer_id(value, path):
    if _parse_writer_id(value, path)[0] != "manager":
        _fail(path, "lead-assignment must be recorded by manager")

def _check_branch_name(value, path):
    _check_text(value, path)
    parts = value.split("/")
    if (any(ord(ch) < 33 or ord(ch) == 127 or ch in "~^:?*[\\" for ch in value)
            or ".." in value or "@{" in value or value.startswith("-")
            or value.endswith(".") or value in {"@", "HEAD"}
            or any(not part or part.startswith(".") or part.endswith(".lock") for part in parts)):
        _fail(path, "invalid branch name")

def _check_relative_path(value, path):
    _check_text(value, path)
    if value.startswith(("/", "\\")) or any(not part or part in {".", ".."} for part in value.replace("\\", "/").split("/")):
        _fail(path, "invalid relative path")

def _is_unknown_obj(value):
    return isinstance(value, dict) and set(value) == {"unknown"}

def _check_unknown_obj(value, path):
    if not _is_unknown_obj(value):
        _fail(path, "expected explicit unknown object")
    _check_text(value["unknown"], f"{path}.unknown")

def _or_unknown(inner):
    def validator(value, path):
        if _is_unknown_obj(value):
            _check_unknown_obj(value, path)
        elif isinstance(value, dict) and "unknown" in value:
            _fail(path, "expected explicit unknown object")
        else:
            inner(value, path)
    return validator

def _nullable(inner):
    return lambda val, p: inner(val, p) if val is not None else None

def _list_of(item_validator, nonempty=False, unique_key=None):
    def validator(value, path):
        if not isinstance(value, list) or (nonempty and not value):
            _fail(path, "expected nonempty list" if nonempty else "expected list")
        seen = set()
        for idx, item in enumerate(value):
            item_path = f"{path}[{idx}]"
            item_validator(item, item_path)
            if unique_key is not None:
                if item[unique_key] in seen:
                    _fail(f"{item_path}.{unique_key}", "duplicate identifier in list")
                seen.add(item[unique_key])
    return validator

def _check_str_or_list(value, path):
    if isinstance(value, str):
        _check_text(value, path)
    elif isinstance(value, list):
        _list_of(_check_text, nonempty=True)(value, path)
    else:
        _fail(path, "expected nonempty string or nonempty list of strings")

def _check_json_value(value, path, depth=0):
    if depth > MAX_JSON_DEPTH:
        _fail(path, "JSON nesting depth exceeded")
    if value is None or isinstance(value, bool):
        return
    if _is_int(value):
        _check_int_digits(value, path)
    elif isinstance(value, float):
        if not math.isfinite(value):
            _fail(path, "expected finite number")
    elif isinstance(value, str):
        if "\x00" in value:
            _fail(path, "NUL character not allowed")
        try:
            value.encode("utf-8")
        except UnicodeError:
            _fail(path, "invalid UTF-8 string")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _check_json_value(item, f"{path}[{idx}]", depth + 1)
    elif _is_unknown_obj(value):
        _check_unknown_obj(value, path)
    elif isinstance(value, dict):
        for idx, (key, item) in enumerate(value.items()):
            _check_text(key, f"{path}[{idx}]")
            _check_json_value(item, f"{path}[{idx}]", depth + 1)
    else:
        _fail(path, "unsupported JSON value")

def _check_json_dict(value, path, nonempty=False):
    if not isinstance(value, dict) or _is_unknown_obj(value) or (nonempty and not value):
        _fail(path, "expected JSON object")
    _check_json_value(value, path, depth=0)
    if len(canonical_json(value)) > MAX_PAYLOAD_BYTES:
        _fail(path, "JSON payload exceeds size limit")

def _check_dict_or_unknown(value, path):
    (_check_unknown_obj if _is_unknown_obj(value) else _check_json_dict)(value, path)

def _check_evidence_payload(value, path):
    if isinstance(value, str):
        _check_text(value, path)
    elif isinstance(value, dict) and not _is_unknown_obj(value):
        _check_json_dict(value, path, nonempty=True)
    else:
        _fail(path, "expected nonempty string or JSON object")

def _object_schema(required=None, optional=None, post_check=None):
    req, opt = dict(required or {}), dict(optional or {})
    allowed = frozenset(req) | frozenset(opt)
    def validator(value, path):
        if not isinstance(value, dict) or _is_unknown_obj(value):
            _fail(path, "expected object")
        for key in value:
            if not isinstance(key, str) or key not in allowed:
                _fail(path, "unknown field")
        for key, check in req.items():
            if key not in value:
                _fail(f"{path}.{key}", "missing required field")
            check(value[key], f"{path}.{key}")
        for key, check in opt.items():
            if key in value:
                check(value[key], f"{path}.{key}")
        if post_check is not None:
            post_check(value, path)
    return validator

_check_acceptance_item = _object_schema({"id": _check_id_token, "text": _check_text})
_check_required_check = _object_schema({"id": _check_id_token, "command": _check_str_or_list, "kind": _check_enum("fast", "heavy")})
_check_checkpoint = _object_schema({"lastSeq": _check_nonneg_int, "digest": _check_digest})

def _check_options_map(value, path):
    if not isinstance(value, dict) or _is_unknown_obj(value):
        _fail(path, "expected options object")
    for idx, (key, opt_val) in enumerate(value.items()):
        _check_text(key, path)
        if isinstance(opt_val, str):
            _check_json_value(opt_val, f"{path}[{idx}]")
        elif not isinstance(opt_val, bool):
            _fail(f"{path}[{idx}]", "option value must be string or boolean")

def _check_selection(value, path):
    if not isinstance(value, dict) or _is_unknown_obj(value):
        _fail(path, "expected selection object")
    has_direct = "providerInstanceId" in value
    if has_direct == ("providerPool" in value):
        _fail(path, "selection must specify exactly one of providerInstanceId or providerPool")
    id_key = "providerInstanceId" if has_direct else "providerPool"
    allowed = {"model", "options", "roles", id_key}
    for key in value:
        if not isinstance(key, str) or key not in allowed:
            _fail(path, "unknown field")
    for req_key in ("model", "options", id_key):
        if req_key not in value:
            _fail(f"{path}.{req_key}", "missing required field")
    _check_text(value["model"], f"{path}.model")
    _check_options_map(value["options"], f"{path}.options")
    _check_text(value[id_key], f"{path}.{id_key}")
    if "roles" in value:
        roles = value["roles"]
        if has_direct:
            _list_of(_check_enum(*_SELECTION_ROLES), nonempty=True)(roles, f"{path}.roles")
            if len(set(roles)) != len(roles):
                _fail(f"{path}.roles", "duplicate role in selection")
        elif roles != ["worker"]:
            _fail(f"{path}.roles", "pool selection roles must be ['worker'] when present")

def _check_probability(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 <= value <= 1):
        _fail(path, "expected finite probability in [0, 1]")

def _choice_answer_validator(options):
    option_set = frozenset(options)
    def check_probs(value, path):
        if not isinstance(value, dict) or set(value) != option_set:
            _fail(path, "probabilities must contain exact option keys")
        total = 0.0
        for key in options:
            _check_probability(value[key], f"{path}.{key}")
            total += float(value[key])
        if not (0.99 <= total <= 1.01):
            _fail(path, "probabilities must sum to 1 within 0.01")
    return _object_schema({"type": _check_enum("choice"), "choice": _check_enum(*options), "probabilities": check_probs, "confidence": _check_probability})

_check_criteria = _object_schema({
    "domain": _object_schema({opt: _check_text for opt in _DOMAIN_OPTIONS}, {"instructions": _check_text}),
    "difficulty": _object_schema({opt: _check_text for opt in _DIFFICULTY_OPTIONS}, {"instructions": _check_text}),
})
_check_classification_answers = _object_schema({
    "domain": _choice_answer_validator(_DOMAIN_OPTIONS),
    "difficulty": _choice_answer_validator(_DIFFICULTY_OPTIONS),
})

def _check_acceptance_entry(value, path):
    (_check_text if isinstance(value, str) else _check_acceptance_item if isinstance(value, dict)
     else _fail)(value if isinstance(value, (str, dict)) else path,
                 path if isinstance(value, (str, dict)) else "expected string or acceptance item")

_check_routing_task = _object_schema({
    "intent": _check_text, "acceptance": _list_of(_check_acceptance_entry, nonempty=True),
    "interfaces": _list_of(_check_text), "scope": _check_str_or_list,
    "riskFacts": _list_of(_check_fact_id), "dependencies": _list_of(_check_text),
})

def _check_routing_context(value, path):
    if value is not None:
        _check_json_dict(value, path, nonempty=True)
        if value.get("priorClassificationRef") is not None:
            _check_pos_int(value["priorClassificationRef"], f"{path}.priorClassificationRef")

def validate_routing_input(value, path="routingInput"):
    """Validate {task, context} and return its canonical sha256:<hex> digest."""
    _object_schema({"task": _check_routing_task, "context": _check_routing_context})(value, path)
    encoded = canonical_json({"task": value["task"], "context": value["context"]})
    if len(encoded) > MAX_ROUTING_INPUT_BYTES:
        _fail(path, "routing input exceeds 16 KiB limit")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

def _post_routing_input(data, path):
    if data["inputDigest"] != validate_routing_input({"task": data["task"], "context": data["context"]}, path=path):
        _fail(f"{path}.inputDigest", "inputDigest does not match canonical routing input")

def _post_routing_classification(data, path):
    if data["outcome"] == "accepted":
        for field in ("snapshotModel", "responseId", "answers"):
            if _is_unknown_obj(data[field]):
                _fail(f"{path}.{field}", "accepted classification requires concrete response fields")
        if data["answers"]["domain"]["choice"] == "MIXED_OR_UNCLEAR":
            _fail(f"{path}.answers.domain.choice", "accepted classification cannot be MIXED_OR_UNCLEAR")
        if data["answers"]["difficulty"]["choice"] == "INSUFFICIENT_CONTEXT":
            _fail(f"{path}.answers.difficulty.choice", "accepted classification cannot be INSUFFICIENT_CONTEXT")

def _post_routing_selection(data, path):
    role, is_pool = data["role"], "providerPool" in data["selection"]
    if role != "text-helper" and data["classificationRef"] is None:
        _fail(f"{path}.classificationRef", "classificationRef required for role")
    if (role == "reviewer") != (data["reviewScope"] is not None):
        _fail(f"{path}.reviewScope", "reviewScope required for reviewer only")
    if role in {"reviewer", "feature-lead"} and (data["tier"] == "easy" or is_pool):
        _fail(path, "reviewer and feature-lead cannot use easy tier or pool selection")
    if is_pool != (data["chosenPoolAccount"] is not None):
        _fail(f"{path}.chosenPoolAccount", "chosenPoolAccount must match pool vs direct selection")

def _check_reviewer(value, path):
    if not isinstance(value, dict) or _is_unknown_obj(value):
        _fail(path, "expected reviewer object")
    kind = value.get("kind")
    if kind == "task":
        _object_schema({"kind": _check_enum("task"), "taskId": _check_text})(value, path)
    elif kind == "lead":
        _object_schema({"kind": _check_enum("lead"), "thread": _check_text})(value, path)
    else:
        _fail(f"{path}.kind", "expected reviewer kind 'task' or 'lead'")

_check_finding = _object_schema(
    required={"id": _check_id_token, "severity": _check_enum("blocking", "important", "minor"), "summary": _check_text},
    optional={"location": _check_text},
)
_check_review_evidence = _object_schema({"path": _check_text, "digest": _check_digest})

def _post_review(data, path):
    if data["reviewer"]["kind"] == "lead" and data.get("evidence") is None:
        _fail(f"{path}.evidence", "lead review requires evidence path and digest")

def _post_finding_disposition(data, path):
    if data["disposition"] == "fixed" and data.get("commit") is None:
        _fail(f"{path}.commit", "fixed disposition requires commit SHA")
    if data["disposition"] == "declined" and data.get("reason") is None:
        _fail(f"{path}.reason", "declined disposition requires reason")

def _check_prior_head(value, path):
    if value is not None and value != "absent":
        _check_commit_sha(value, path)

def _post_op_intent(data, path):
    if data["operation"] == "push" and data["expectedPriorHead"] is None:
        _fail(f"{path}.expectedPriorHead", "push op-intent requires expectedPriorHead SHA or 'absent'")

def _check_child_status(value, path):
    if value is not None and (not _is_int(value) or not (0 <= value <= 255)):
        _fail(path, "expected child exit status in 0..255 or null")

def _post_status_summary(data, path):
    if data["childStarted"]:
        if data.get("refusal") is not None:
            _fail(f"{path}.refusal", "refusal must be null when childStarted is true")
    elif data.get("childStatus") is not None or data.get("refusal") is None:
        _fail(path, "childStarted false requires null childStatus and non-null refusal")

_check_status_summary = _object_schema(
    required={"childStarted": _check_bool},
    optional={"childStatus": _check_child_status, "revocation": _nullable(_check_enum("ok", "failed")), "refusal": _nullable(_check_text)},
    post_check=_post_status_summary,
)

def _post_decision(data, path):
    if not _is_unknown_obj(data["answer"]):
        for field in ("decidedBy", "date"):
            if _is_unknown_obj(data[field]):
                _fail(f"{path}.{field}", f"resolved decision requires concrete {field}")

def _check_decision_ref(value, path):
    (_check_pos_int if _is_int(value) else _check_text if isinstance(value, str)
     else _fail)(value if _is_int(value) or isinstance(value, str) else path,
                 path if _is_int(value) or isinstance(value, str) else "expected positive integer or string")

def _post_scope(data, path):
    if all(data.get(key) is None for key in ("scope", "acceptance", "requiredChecks")):
        _fail(path, "scope event must replace at least one of scope, acceptance or requiredChecks")

def _post_restart(data, path):
    before_ref = data.get("beforeRef")
    if data["phase"] == "before" and before_ref is not None:
        _fail(f"{path}.beforeRef", "before-phase restart cannot set beforeRef")
    if data["phase"] == "after" and (before_ref is None or before_ref <= data["checkpoint"]["lastSeq"]):
        _fail(f"{path}.beforeRef", "after-phase restart requires beforeRef > checkpoint.lastSeq")

def _post_writer_transfer(data, path):
    _, _, from_repo = _parse_writer_id(data["from"], f"{path}.from")
    _, _, to_repo = _parse_writer_id(data["to"], f"{path}.to")
    if data["from"] == data["to"] or from_repo != to_repo:
        _fail(f"{path}.to", "writer-transfer requires distinct writers on same repository")
    if data["reconciliationRef"] > data["checkpoint"]["lastSeq"]:
        _fail(f"{path}.reconciliationRef", "reconciliationRef must be within checkpoint.lastSeq")

def _spec(required, optional=None, post=None):
    return MappingProxyType({"required": MappingProxyType(required), "optional": MappingProxyType(optional or {}), "post": post})

EVENT_KINDS = (
    "routing-input", "routing-classification", "routing-judgment", "routing-selection",
    "dispatch-intent", "dispatch-confirmed", "result", "check", "criterion-evidence",
    "lead-assignment", "review", "finding-disposition", "op-intent", "op-result",
    "decision", "scope", "next-action", "reconciliation", "restart", "handoff", "writer-transfer",
)

EVENT_SCHEMAS = MappingProxyType({
    "routing-input": _spec({"task": _check_routing_task, "context": _check_routing_context, "inputDigest": _check_digest, "policyDigest": _check_digest}, post=_post_routing_input),
    "routing-classification": _spec({
        "inputRef": _check_pos_int, "inputDigest": _check_digest, "policyDigest": _check_digest,
        "ruleVersion": _check_text, "criteria": _check_criteria, "criteriaDigest": _check_digest,
        "riskFacts": _list_of(_check_fact_id), "configuredModel": _check_text,
        "snapshotModel": _or_unknown(_check_text), "responseId": _or_unknown(_check_text),
        "answers": _or_unknown(_check_classification_answers),
        "outcome": _check_enum("accepted", "retry-with-context", "manager-judgment"), "reason": _check_text,
    }, post=_post_routing_classification),
    "routing-judgment": _spec({
        "inputRef": _check_pos_int, "inputDigest": _check_digest, "policyDigest": _check_digest,
        "domain": _check_enum("SOFTWARE_ENGINEERING", "OTHER"), "difficulty": _check_enum("EASY", "MEDIUM", "CHALLENGING"),
        "reason": _check_text, "basis": _check_enum("jev-failure", "low-confidence", "mixed-or-unclear", "manual"),
    }),
    "routing-selection": _spec({
        "inputRef": _check_pos_int, "inputDigest": _check_digest, "policyDigest": _check_digest,
        "classificationRef": _nullable(_check_pos_int), "role": _check_enum(*_ROLES), "round": _check_enum("first", "corrections"),
        "reviewScope": _nullable(_check_enum("code", "document", "corrections-delta")),
        "tier": _check_enum("easy", "medium", "challenging"), "floorsApplied": _list_of(_check_fact_id), "escalation": _check_bool,
        "selection": _check_selection, "chosenPoolAccount": _nullable(_check_text),
        "skippedPoolAccounts": _list_of(_object_schema({"providerInstanceId": _check_text, "reason": _check_text})),
        "availabilityReason": _check_text, "fallbackReason": _nullable(_check_text), "configDigest": _check_digest,
        "catalogReadAt": _check_utc_timestamp, "catalogDigest": _check_digest, "quotaStateDigest": _check_digest,
    }, post=_post_routing_selection),
    "dispatch-intent": _spec({
        "role": _check_enum(*_ROLES), "round": _check_pos_int, "clientRequestId": _check_work_id,
        "briefPath": _check_text, "briefDigest": _check_digest, "selection": _check_selection,
        "provisionedCommit": _check_commit_sha, "startCommit": _check_commit_sha, "reviewBase": _check_commit_sha,
        "dispatchingCheckout": _check_text, "isolation": _check_enum("worktree", "shared"),
    }),
    "dispatch-confirmed": _spec({"clientRequestId": _check_work_id, "taskId": _check_text, "childThread": _check_text, "worktreePath": _or_unknown(_check_text)}),
    "result": _spec({
        "taskId": _check_text,
        "t3Status": _object_schema({"state": _check_enum("completed", "failed", "cancelled", "interrupted"), "summary": _check_text}, {"turnId": _check_text}),
        "worktreePath": _check_text, "resultCommit": _check_commit_sha, "parentCommit": _check_commit_sha,
        "branchObserved": _or_unknown(_check_branch_name), "workerClaims": _check_json_dict,
        "verifiedFacts": _check_json_dict, "questions": _list_of(_check_text),
    }),
    "check": _spec({
        "checkId": _check_id_token, "command": _check_str_or_list, "commit": _check_commit_sha,
        "outcome": _check_enum("pass", "fail", "refused", "blocked"), "source": _check_enum(*_CHECK_SOURCES), "evidencePath": _check_text,
    }),
    "criterion-evidence": _spec({"criterionId": _check_id_token, "commit": _check_commit_sha, "source": _check_enum(*_CHECK_SOURCES), "evidence": _check_evidence_payload}),
    "lead-assignment": _spec({"thread": _check_text, "assignedBy": _check_manager_writer_id, "date": _check_utc_date, "reason": _check_text}),
    "review": _spec(
        {"reviewer": _check_reviewer, "base": _check_commit_sha, "head": _check_commit_sha, "verdict": _check_enum("approve", "approve-after-fixes", "changes-required"), "findings": _list_of(_check_finding, unique_key="id")},
        {"evidence": _nullable(_check_review_evidence)}, post=_post_review,
    ),
    "finding-disposition": _spec(
        {
            "reviewRef": _check_pos_int, "findingId": _check_id_token, "response": _check_text,
            "disposition": _check_enum("fixed", "resolved-outside-tree", "declined", "withdrawn", "disputed", "rejected-by-second-review", "owner-decided"),
            "by": _check_text, "evidence": _check_evidence_payload,
        },
        {"commit": _nullable(_check_commit_sha), "reason": _nullable(_check_text)}, post=_post_finding_disposition,
    ),
    "op-intent": _spec({"opId": _check_id_token, "operation": _check_enum("push", "pull-request", "other"), "target": _check_text, "commit": _check_commit_sha, "expectedPriorHead": _check_prior_head}, post=_post_op_intent),
    "op-result": _spec(
        {
            "intentRef": _check_pos_int, "opId": _check_id_token, "operation": _check_enum("push", "pull-request", "other"),
            "target": _check_text, "commit": _check_commit_sha,
            "outcome": _check_enum("succeeded", "no-op", "failed", "refused", "unknown"), "confirmation": _check_text,
        },
        {"statusSummary": _nullable(_check_status_summary)},
    ),
    "decision": _spec({"question": _check_text, "answer": _or_unknown(_check_text), "decidedBy": _or_unknown(_check_text), "date": _or_unknown(_check_utc_date)}, post=_post_decision),
    "scope": _spec(
        {"decisionRef": _check_decision_ref},
        {
            "scope": _nullable(_check_str_or_list),
            "acceptance": _nullable(_list_of(_check_acceptance_item, nonempty=True, unique_key="id")),
            "requiredChecks": _nullable(_list_of(_check_required_check, nonempty=True, unique_key="id")),
        },
        post=_post_scope,
    ),
    "next-action": _spec({"action": _or_unknown(_check_text)}),
    "reconciliation": _spec(
        {
            "item": _object_schema({"kind": _check_enum("dispatch", "operation", "lead", "worktree", "temp-file", "takeover"), "ref": _check_decision_ref}),
            "t3Facts": _check_dict_or_unknown, "gitFacts": _check_dict_or_unknown,
            "outcome": _check_enum("completed", "stopped-and-preserved", "not-started", "in-flight", "unknown-liveness", "orphaned-temp-repaired"),
        },
        {"notes": _nullable(_check_text)},
    ),
    "restart": _spec({"phase": _check_enum("before", "after"), "checkpoint": _check_checkpoint}, {"beforeRef": _nullable(_check_pos_int)}, post=_post_restart),
    "handoff": _spec({
        "pointers": _object_schema(
            {"skill": _check_text, "repository": _check_repo_identity, "recordsPath": _check_text, "routingPolicyPath": _check_text},
            {"takeoverDumpPath": _nullable(_check_text)},
        ),
        "holdEndCondition": _check_text, "pilotCriteria": _list_of(_check_text, nonempty=True),
        "nextAction": _or_unknown(_check_text), "checkpoint": _check_checkpoint,
    }),
    "writer-transfer": _spec({"from": _check_writer_id, "to": _check_writer_id, "reconciliationRef": _check_pos_int, "checkpoint": _check_checkpoint}, post=_post_writer_transfer),
})

_EVENT_VALIDATORS = {kind: _object_schema(spec["required"], spec["optional"], spec["post"]) for kind, spec in EVENT_SCHEMAS.items()}

def _post_work(work, path):
    role, lead_work, writer_repo = _parse_writer_id(work["writer"], f"{path}.writer")
    if writer_repo != work["repository"]:
        _fail(f"{path}.writer", "writer repository does not match work.repository")
    if role == "lead" and lead_work != work["workId"]:
        _fail(f"{path}.writer", "lead writer work ID does not match work.workId")

_WORK_VALIDATOR = _object_schema(
    required={
        "workId": _check_work_id, "linearIssue": _nullable(_check_text), "repository": _check_repo_identity,
        "targetBranch": _check_branch_name, "reviewBase": _check_commit_sha, "scope": _check_str_or_list,
        "designRefs": _list_of(_check_text), "ownedPaths": _list_of(_check_relative_path, nonempty=True),
        "dependencies": _list_of(_check_text),
        "acceptance": _list_of(_check_acceptance_item, nonempty=True, unique_key="id"),
        "requiredChecks": _list_of(_check_required_check, nonempty=True, unique_key="id"),
        "writer": _check_writer_id,
    },
    post_check=_post_work,
)

def validate_work(value):
    """Validate a parsed work.json structure or raise SchemaError."""
    _WORK_VALIDATOR(value, "work")
    canonical_json(value)

def validate_event_data(kind, data, path="event.data"):
    """Validate an event's kind and data payload or raise SchemaError."""
    if not isinstance(kind, str) or kind not in _EVENT_VALIDATORS:
        _fail("event.kind", "unknown event kind")
    _EVENT_VALIDATORS[kind](data, path)
    canonical_json(data)

def _check_schema_version(value, path):
    if not _is_int(value) or value != SCHEMA_VERSION:
        _fail(path, "unsupported schemaVersion")

def _post_event(event, path):
    kind, seq, data = event["kind"], event["seq"], event["data"]
    validate_event_data(kind, data, path=f"{path}.data")
    if kind in {"restart", "handoff", "writer-transfer"} and data["checkpoint"]["lastSeq"] >= seq:
        _fail(f"{path}.data.checkpoint.lastSeq", "checkpoint.lastSeq must precede checkpoint-bearing event seq")
    if kind == "restart" and data["phase"] == "after" and data["beforeRef"] >= seq:
        _fail(f"{path}.data.beforeRef", "beforeRef must precede after-phase restart seq")

_EVENT_ENVELOPE_VALIDATOR = _object_schema(
    required={
        "schemaVersion": _check_schema_version, "seq": _check_pos_int, "kind": _check_enum(*EVENT_KINDS),
        "at": _check_utc_timestamp, "writer": _check_writer_id, "helperCommit": _check_commit_sha,
        "data": lambda val, p: _fail(p, "expected object") if not isinstance(val, dict) else None,
    },
    post_check=_post_event,
)

def validate_event(value):
    """Validate a parsed event envelope and its kind-specific data."""
    _EVENT_ENVELOPE_VALIDATOR(value, "event")
    canonical_json(value)
