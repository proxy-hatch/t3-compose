"""Strict validation and read-only loading for routing config and quota state."""
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from .cli import ExitCode, Outcome
from .jsonio import canonical_json, read_json_with_digest
from .paths import local_dir

DEFAULT_CONFIG_NAME, DEFAULT_STATE_NAME = "agent-routing.json", "agent-routing-state.json"
TOP_LEVEL_STATE_KEYS = ("activeProviderInstanceId", "quotaObservations", "note")
REFUSAL_KINDS = ("usage-limit", "quota-exhausted", "credits", "rate-limit")
REFUSAL_KEYS = ("providerInstanceId", "model", "kind", "observedAt", "resetAt", "evidence")
OBSERVATION_KEYS = ("observedAt", "status", "resetAt", "evidence")
JEV_KEYS = ("provider", "model", "minConfidence", "maxContextRetries", "timeoutSeconds", "keyFile")
VERSION_MIGRATION = "Migrate routing configuration to version 3; see docs/configuration.md."
FACT_ID_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*$")

MATRIX_LANES = MappingProxyType({
    "feature-lead": ("default",), "worker": ("backend-logic", "ui-frontend"),
    "reviewer": ("non-critical", "core"),
})
MATRIX_STAGES = ("1", "2", "3")
HELPER_NAMES = ("verifier", "text")
ROLE_BLOCK_ROLES = ("feature-lead", "worker", "reviewer", "verifier", "text")
DRIVER_KINDS = ("claudeAgent", "codex", "antigravity", "pi")
MATRIX_CONFIG_KEYS = ("version", "manager", "matrix", "helpers", "providerPools", "roleBlocks",
                      "quota", "classification", "riskFacts", "escalation", "jev")


@dataclass(frozen=True)
class LoadedConfig:
    data: Mapping[str, Any]
    digest: str
    policy_digest: str
    notices: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class LoadedQuotaState:
    data: Mapping[str, Any]
    digest: str


class ConfigValidationError(Exception):
    """Sanitized validation failure carrying structured errors for ExitCode.INVALID_CONFIG."""

    def __init__(self, errors):
        self.errors = tuple(freeze_value(err) for err in errors)
        msg = "invalid routing configuration; errors at: " + ", ".join(f"{e['path']} ({e['rule']})" for e in errors)
        if self._needs_migration():
            msg += "; " + VERSION_MIGRATION
        super().__init__(msg)

    def _needs_migration(self):
        return any(e["path"] == "version" and e["rule"] == "expected-version-3" for e in self.errors)

    def to_payload(self):
        payload = {"valid": False, "errors": [thaw_value(e) for e in self.errors]}
        if self._needs_migration():
            payload["migration"] = VERSION_MIGRATION
        return payload


def freeze_value(v):
    if isinstance(v, Mapping):
        return MappingProxyType({k: freeze_value(val) for k, val in v.items()})
    return tuple(freeze_value(x) for x in v) if isinstance(v, (list, tuple)) else v


def thaw_value(v):
    if isinstance(v, Mapping):
        return {k: thaw_value(val) for k, val in v.items()}
    return [thaw_value(x) for x in v] if isinstance(v, tuple) else v


def _nonempty_str(v):
    return isinstance(v, str) and bool(v.strip())


def _int_not_bool(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num_not_bool(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (isinstance(v, int) or math.isfinite(v))


def _err(errors, path, rule):
    errors.append({"path": path, "rule": rule})


def _check_str_field(obj, key, path, errors, rule="invalid-string"):
    if key in obj and not _nonempty_str(obj[key]):
        _err(errors, f"{path}.{key}" if path else key, rule)


def _check_keys(obj, path, req_keys, opt_keys, errors):
    if not isinstance(obj, dict):
        _err(errors, path, "expected-object")
        return False
    allowed = set(req_keys) | set(opt_keys)
    for k in sorted(obj):
        if k not in allowed:
            _err(errors, k if path == "$" else f"{path}.{k}", "unknown-key")
    for k in req_keys:
        if k not in obj:
            _err(errors, k if path == "$" else f"{path}.{k}", "missing-key")
    return True


def _validate_options(options, path, errors):
    if not isinstance(options, dict):
        _err(errors, path, "expected-object")
        return
    for k in sorted(options):
        if not _nonempty_str(k):
            _err(errors, path, "invalid-option-key")
        elif not isinstance(options[k], (str, bool)):
            _err(errors, f"{path}.{k}", "invalid-option-value")


def _validate_pools(raw, errors):
    pools, pool_members = {}, set()
    if "providerPools" in raw:
        raw_pools = raw["providerPools"]
        if not isinstance(raw_pools, dict) or not raw_pools:
            _err(errors, "providerPools", "expected-object" if not isinstance(raw_pools, dict) else "empty-object")
        else:
            for p_name in sorted(raw_pools):
                p_path, p_obj = f"providerPools.{p_name}", raw_pools[p_name]
                if not _nonempty_str(p_name):
                    _err(errors, p_path, "invalid-pool-name")
                elif _check_keys(p_obj, p_path, ("selection", "providerInstanceIds"), (), errors):
                    if p_obj.get("selection") != "exhaust-in-order":
                        _err(errors, f"{p_path}.selection", "invalid-pool-selection-mode")
                    ids = p_obj.get("providerInstanceIds")
                    if not isinstance(ids, list) or not ids:
                        _err(errors, f"{p_path}.providerInstanceIds", "expected-list" if not isinstance(ids, list) else "empty-list")
                    else:
                        seen, valid_ids = set(), []
                        for idx, inst in enumerate(ids):
                            ip = f"{p_path}.providerInstanceIds[{idx}]"
                            if not _nonempty_str(inst):
                                _err(errors, ip, "invalid-string")
                            elif inst in seen or inst in pool_members:
                                _err(errors, ip, "duplicate-instance-id" if inst in seen else "duplicate-pool-member")
                            else:
                                seen.add(inst)
                                pool_members.add(inst)
                                valid_ids.append(inst)
                        pools[p_name] = valid_ids
    return pools, pool_members


def _validate_jev(raw, keys, errors):
    if "jev" in raw and _check_keys(raw["jev"], "jev", keys, (), errors):
        raw_jev = raw["jev"]
        if "provider" in raw_jev and raw_jev["provider"] != "openrouter":
            _err(errors, "jev.provider", "invalid-jev-provider")
        _check_str_field(raw_jev, "model", "jev", errors)
        if "minConfidence" in raw_jev and (not _num_not_bool(raw_jev["minConfidence"]) or not (0 < raw_jev["minConfidence"] <= 1)):
            _err(errors, "jev.minConfidence", "invalid-min-confidence")
        if "maxContextRetries" in raw_jev and (not _int_not_bool(raw_jev["maxContextRetries"]) or raw_jev["maxContextRetries"] not in (0, 1)):
            _err(errors, "jev.maxContextRetries", "invalid-max-context-retries")
        if "timeoutSeconds" in raw_jev and (not _int_not_bool(raw_jev["timeoutSeconds"]) or not (1 <= raw_jev["timeoutSeconds"] <= 120)):
            _err(errors, "jev.timeoutSeconds", "invalid-timeout-seconds")
        if "keyFile" in raw_jev and _check_keys(raw_jev["keyFile"], "jev.keyFile", ("path", "field"), (), errors):
            kf = raw_jev["keyFile"]
            if "path" in kf and (not _nonempty_str(kf["path"]) or not kf["path"].startswith("/")):
                _err(errors, "jev.keyFile.path", "expected-absolute-path")
            _check_str_field(kf, "field", "jev.keyFile", errors)


def validate_config_data(raw):
    """Validate version 3 only; older and malformed versions require migration."""
    if not isinstance(raw, dict):
        raise ConfigValidationError([{"path": "$", "rule": "expected-object"}])
    version = raw.get("version")
    if not _int_not_bool(version) or version != 3:
        raise ConfigValidationError([{"path": "version", "rule": "expected-version-3"}])
    return _validate_matrix_config(raw)


def _validate_matrix_selection(obj, path, pools, members, errors, direct=False):
    before = len(errors)
    if not _check_keys(obj, path, ("model", "options"), ("providerInstanceId", "providerPool"), errors):
        return None
    has_direct, has_pool = "providerInstanceId" in obj, "providerPool" in obj
    if has_direct == has_pool:
        _err(errors, path, "disjoint-selection-keys" if has_direct else "missing-selection-target")
    _check_str_field(obj, "model", path, errors)
    if "options" in obj:
        _validate_options(obj["options"], f"{path}.options", errors)
    if has_pool:
        name = obj["providerPool"]
        if not _nonempty_str(name):
            _err(errors, f"{path}.providerPool", "invalid-string")
        elif name not in pools:
            _err(errors, f"{path}.providerPool", "unknown-provider-pool")
        if direct:
            _err(errors, f"{path}.providerPool", "expected-direct-selection")
        if "options" in obj and obj["options"] != {}:
            _err(errors, f"{path}.options", "pool-options-must-be-empty")
    if has_direct:
        name = obj["providerInstanceId"]
        if not _nonempty_str(name):
            _err(errors, f"{path}.providerInstanceId", "invalid-string")
        elif name in members:
            _err(errors, f"{path}.providerInstanceId", "direct-selection-names-pool-member")
    if len(errors) == before:
        return ("direct" if has_direct else "pool", name, obj["model"], canonical_json(obj["options"]))
    return None


def _validate_cell(obj, path, pools, members, errors, *, reviewer=False, direct=False):
    if not _check_keys(obj, path, ("primary", "quotaFallback"), ("prePass",), errors):
        return
    primary = None
    if "primary" in obj:
        primary = _validate_matrix_selection(obj["primary"], f"{path}.primary", pools, members, errors, direct)
    if "quotaFallback" in obj:
        fallbacks = obj["quotaFallback"]
        if not isinstance(fallbacks, list):
            _err(errors, f"{path}.quotaFallback", "expected-list")
        else:
            seen = set()
            for i, sel in enumerate(fallbacks):
                fp = f"{path}.quotaFallback[{i}]"
                identity = _validate_matrix_selection(sel, fp, pools, members, errors, direct)
                if identity is not None:
                    if identity == primary:
                        _err(errors, fp, "fallback-equals-primary")
                    if identity in seen:
                        _err(errors, fp, "duplicate-fallback")
                    seen.add(identity)
    if "prePass" in obj:
        if reviewer:
            _validate_matrix_selection(obj["prePass"], f"{path}.prePass", pools, members, errors)
        else:
            _err(errors, f"{path}.prePass", "pre-pass-outside-reviewer-cell")


def _validate_matrix_config(raw):
    errors = []
    _check_keys(raw, "$", MATRIX_CONFIG_KEYS, (), errors)
    pools, members = _validate_pools(raw, errors)
    if "manager" in raw:
        _validate_cell(raw["manager"], "manager", pools, members, errors, direct=True)
    if "matrix" in raw and _check_keys(raw["matrix"], "matrix", MATRIX_LANES, (), errors):
        for role, lanes in MATRIX_LANES.items():
            rp = f"matrix.{role}"
            if role in raw["matrix"] and _check_keys(raw["matrix"][role], rp, lanes, (), errors):
                for lane in lanes:
                    lp = f"{rp}.{lane}"
                    lane_obj = raw["matrix"][role].get(lane)
                    if lane in raw["matrix"][role] and _check_keys(lane_obj, lp, MATRIX_STAGES, (), errors):
                        for stage in MATRIX_STAGES:
                            if stage in lane_obj:
                                _validate_cell(lane_obj[stage], f"{lp}.{stage}", pools, members, errors, reviewer=role == "reviewer")
    if "helpers" in raw and _check_keys(raw["helpers"], "helpers", HELPER_NAMES, (), errors):
        for name in HELPER_NAMES:
            if name in raw["helpers"]:
                _validate_cell(raw["helpers"][name], f"helpers.{name}", pools, members, errors)
    if "roleBlocks" in raw:
        blocks, seen = raw["roleBlocks"], set()
        if not isinstance(blocks, list):
            _err(errors, "roleBlocks", "expected-list")
        else:
            for i, block in enumerate(blocks):
                bp = f"roleBlocks[{i}]"
                if _check_keys(block, bp, ("driverKind", "role", "reason", "evidence"), (), errors):
                    for k in ("driverKind", "role", "reason", "evidence"):
                        _check_str_field(block, k, bp, errors)
                    for k, allowed, rule in (("driverKind", DRIVER_KINDS, "invalid-driver-kind"),
                                              ("role", ROLE_BLOCK_ROLES, "invalid-role-block-role")):
                        if k in block and block[k] not in allowed:
                            _err(errors, f"{bp}.{k}", rule)
                    pair = (block.get("driverKind"), block.get("role"))
                    if all(_nonempty_str(v) for v in pair):
                        if pair in seen:
                            _err(errors, bp, "duplicate-role-block")
                        seen.add(pair)
    for section, fields in (("quota", (("refusalRetryMinutes", 1440, "invalid-refusal-retry-minutes"),)),
                             ("escalation", (("repairAttempts", 10, "invalid-repair-attempts"),
                                             ("correctionsRoundsPerFinding", 10, "invalid-corrections-rounds-per-finding")))):
        if section in raw and _check_keys(raw[section], section, tuple(k for k, _, _ in fields), (), errors):
            for k, maximum, rule in fields:
                if k in raw[section] and (not _int_not_bool(raw[section][k]) or not 1 <= raw[section][k] <= maximum):
                    _err(errors, f"{section}.{k}", rule)
    questions = {"lane": ("BACKEND_LOGIC", "UI_FRONTEND", "MIXED"), "criticality": ("CORE", "NON_CRITICAL"),
                 "scope": ("STANDARD", "COMPLEX", "INSUFFICIENT_CONTEXT")}
    if "classification" in raw and _check_keys(raw["classification"], "classification", ("ruleVersion", *questions), (), errors):
        cls = raw["classification"]
        _check_str_field(cls, "ruleVersion", "classification", errors)
        for name, criteria in questions.items():
            qp = f"classification.{name}"
            if name in cls and _check_keys(cls[name], qp, ("instructions", "criteria"), (), errors):
                _check_str_field(cls[name], "instructions", qp, errors)
                if "criteria" in cls[name] and _check_keys(cls[name]["criteria"], f"{qp}.criteria", criteria, (), errors):
                    for key in criteria:
                        _check_str_field(cls[name]["criteria"], key, f"{qp}.criteria", errors)
    if "riskFacts" in raw:
        facts = raw["riskFacts"]
        if not isinstance(facts, dict) or not facts:
            _err(errors, "riskFacts", "expected-object" if not isinstance(facts, dict) else "empty-object")
        else:
            for fid, fact in sorted(facts.items()):
                fp = f"riskFacts.{fid}"
                if not isinstance(fid, str) or not FACT_ID_RE.fullmatch(fid):
                    _err(errors, fp, "invalid-fact-id")
                if _check_keys(fact, fp, ("description",), ("scope", "criticality"), errors):
                    _check_str_field(fact, "description", fp, errors)
                    for key, expected in (("scope", "COMPLEX"), ("criticality", "CORE")):
                        if key in fact and fact[key] != expected:
                            _err(errors, f"{fp}.{key}", "invalid-floor")
    _validate_jev(raw, JEV_KEYS, errors)
    if errors:
        raise ConfigValidationError(errors)
    return freeze_value(raw), ()


def routing_policy_projection(config_data):
    """Project exact config fields that govern classification and risk-floor freshness."""
    jev = config_data["jev"]
    return {
        "classification": thaw_value(config_data["classification"]),
        "riskFacts": thaw_value(config_data["riskFacts"]),
        "jev": {k: jev[k] for k in ("provider", "model", "minConfidence", "maxContextRetries")},
    }


def compute_policy_digest(config_data):
    return "sha256:" + hashlib.sha256(canonical_json(routing_policy_projection(config_data))).hexdigest()


def validate_quota_state_data(raw, config_data=None):
    errors = []
    if not isinstance(raw, dict):
        raise ConfigValidationError([{"path": "$", "rule": "expected-object"}])
    _check_keys(raw, "$", TOP_LEVEL_STATE_KEYS, ("refusals",), errors)
    pool_members = {i for p in config_data["providerPools"].values() for i in p["providerInstanceIds"]} if config_data and "providerPools" in config_data else set()
    if "activeProviderInstanceId" in raw:
        active = raw["activeProviderInstanceId"]
        if not _nonempty_str(active) or (pool_members and active not in pool_members):
            _err(errors, "activeProviderInstanceId", "invalid-string" if not _nonempty_str(active) else "unknown-pool-member")
    if "note" in raw and not isinstance(raw["note"], str):
        _err(errors, "note", "invalid-string")
    if "quotaObservations" in raw:
        obs_map = raw["quotaObservations"]
        if not isinstance(obs_map, dict):
            _err(errors, "quotaObservations", "expected-object")
        else:
            for inst_id in sorted(obs_map):
                inst_path, obs_list = f"quotaObservations.{inst_id}", obs_map[inst_id]
                if not _nonempty_str(inst_id):
                    _err(errors, inst_path, "invalid-instance-id")
                    continue
                if pool_members and inst_id not in pool_members:
                    _err(errors, inst_path, "unknown-pool-member")
                if not isinstance(obs_list, list):
                    _err(errors, inst_path, "expected-list")
                    continue
                for idx, entry in enumerate(obs_list):
                    ep = f"{inst_path}[{idx}]"
                    if _check_keys(entry, ep, OBSERVATION_KEYS, (), errors):
                        _check_str_field(entry, "observedAt", ep, errors)
                        if "status" in entry and entry["status"] not in ("exhausted", "available"):
                            _err(errors, f"{ep}.status", "invalid-quota-status")
                        if "resetAt" in entry and entry["resetAt"] is not None and not _nonempty_str(entry["resetAt"]):
                            _err(errors, f"{ep}.resetAt", "invalid-reset-at")
                        _check_str_field(entry, "evidence", ep, errors)
    if "refusals" in raw:
        if not isinstance(raw["refusals"], list):
            _err(errors, "refusals", "expected-list")
        else:
            for idx, entry in enumerate(raw["refusals"]):
                ep = f"refusals[{idx}]"
                if _check_keys(entry, ep, REFUSAL_KEYS, (), errors):
                    for key in ("providerInstanceId", "model", "observedAt", "evidence"):
                        _check_str_field(entry, key, ep, errors)
                    if "kind" in entry and entry["kind"] not in REFUSAL_KINDS:
                        _err(errors, f"{ep}.kind", "invalid-refusal-kind")
                    if "resetAt" in entry and entry["resetAt"] is not None and not _nonempty_str(entry["resetAt"]):
                        _err(errors, f"{ep}.resetAt", "invalid-reset-at")
    if errors:
        raise ConfigValidationError(errors)
    return freeze_value(raw)


def _read_bounded_json(path, label):
    try:
        return read_json_with_digest(path)
    except OSError:
        raise ConfigValidationError([{"path": label, "rule": "unreadable-file"}]) from None
    except ValueError as exc:
        raise ConfigValidationError([{"path": label, "rule": "size-limit-exceeded" if "limit exceeded" in str(exc) else "invalid-json"}]) from None


def _load_config(path=None, *, local=None):
    target = Path(path) if path is not None else (Path(local) if local is not None else local_dir()) / DEFAULT_CONFIG_NAME
    raw, digest = _read_bounded_json(target, "config")
    frozen, notices = validate_config_data(raw)
    return LoadedConfig(data=frozen, digest=digest, policy_digest=compute_policy_digest(frozen), notices=notices)


def load_config(path=None, *, local=None):
    """Compatibility alias for the bounded, uncached version-3 matrix loader."""
    return load_matrix_config(path, local=local)


def load_matrix_config(path=None, *, local=None) -> LoadedConfig:
    """Load version-3 config in one bounded read, without caching or opening keys."""
    return _load_config(path, local=local)


def load_quota_state(path=None, *, local=None, config=None):
    """Read, validate, and return deeply read-only quota state and digest without caching."""
    target = Path(path) if path is not None else (Path(local) if local is not None else local_dir()) / DEFAULT_STATE_NAME
    raw, digest = _read_bounded_json(target, "state")
    try:
        frozen = validate_quota_state_data(raw, config_data=config.data if isinstance(config, LoadedConfig) else config)
    except ConfigValidationError as exc:
        raise ConfigValidationError([
            {"path": "state" if e["path"] == "$" else f"state.{e['path']}", "rule": e["rule"]} for e in exc.errors
        ]) from None
    return LoadedQuotaState(data=frozen, digest=digest)


def check_config_command(args):
    config_path = Path(args.config) if args.config is not None else args.local_dir / DEFAULT_CONFIG_NAME
    state_path = Path(args.state) if args.state is not None else args.local_dir / DEFAULT_STATE_NAME
    errors, loaded_cfg, loaded_state = [], None, None
    try:
        loaded_cfg = _load_config(config_path)
    except ConfigValidationError as exc:
        errors.extend(thaw_value(e) for e in exc.errors)
    try:
        loaded_state = load_quota_state(state_path, config=loaded_cfg.data if loaded_cfg is not None else None)
    except ConfigValidationError as exc:
        errors.extend(thaw_value(e) for e in exc.errors)
    if errors:
        return Outcome(ConfigValidationError(errors).to_payload(), ExitCode.INVALID_CONFIG)
    return {
        "version": 3, "digest": loaded_cfg.digest, "policyDigest": loaded_cfg.policy_digest,
        "stateDigest": loaded_state.digest,
        "ruleVersion": loaded_cfg.data["classification"]["ruleVersion"],
        "riskFacts": {fid: {k: info[k] for k in ("scope", "criticality") if k in info}
                      for fid, info in loaded_cfg.data["riskFacts"].items()},
        "notices": [],
    }


def register(registry):
    parser = registry.group("config").add_parser("check")
    parser.add_argument("--config", default=None)
    parser.add_argument("--state", default=None)
    parser.set_defaults(handler=check_config_command)
