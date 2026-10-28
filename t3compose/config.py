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
TOP_LEVEL_CONFIG_KEYS = (
    "version", "master", "modelPreferences", "easyWorker", "textHelper",
    "providerPools", "classification", "riskFacts", "roundTiers", "jev", "availabilityFallback",
)
VERSION_2_NEW_SECTIONS = ("classification", "riskFacts", "roundTiers", "jev", "availabilityFallback")
TOP_LEVEL_STATE_KEYS = ("activeProviderInstanceId", "quotaObservations", "note")
OBSERVATION_KEYS = ("observedAt", "status", "resetAt", "evidence")
DOMAIN_CRITERIA_KEYS = ("SOFTWARE_ENGINEERING", "OTHER", "MIXED_OR_UNCLEAR")
DIFFICULTY_CRITERIA_KEYS = ("EASY", "MEDIUM", "CHALLENGING", "INSUFFICIENT_CONTEXT")
PREFERENCE_DOMAINS = ("software-engineering", "non-engineering")
PREFERENCE_TIERS = ("medium", "challenging")
ROUND_TIER_KEYS = ("corrections", "correctionsReview", "documentReview")
FALLBACK_ROLES = ("worker", "reviewer", "feature-lead")
JEV_KEYS = ("provider", "model", "minConfidence", "maxContextRetries", "uncertainResult", "timeoutSeconds", "keyFile")
FALLBACK_KEYS = ("roles", "manager", "chains", "recordReason", "reportReason")
FACT_ID_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*$")


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

    def __init__(self, errors, missing_sections=()):
        self.errors = tuple(freeze_value(err) for err in errors)
        self.missing_sections = tuple(missing_sections)
        msg = "invalid routing configuration; errors at: " + ", ".join(f"{e['path']} ({e['rule']})" for e in errors)
        if self.missing_sections:
            msg += "; missing version-2 sections: " + ", ".join(self.missing_sections)
        super().__init__(msg)

    def to_payload(self):
        payload = {"valid": False, "errors": [thaw_value(e) for e in self.errors]}
        if self.missing_sections:
            payload["missingSections"] = list(self.missing_sections)
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


def _collect_missing_v2_sections(raw):
    if not isinstance(raw, dict):
        return ()
    missing = []
    for sec in VERSION_2_NEW_SECTIONS:
        sec_obj = raw.get(sec)
        if not isinstance(sec_obj, dict):
            missing.append(sec)
        elif sec == "classification":
            for req in ("ruleVersion", "domain", "difficulty"):
                if req not in sec_obj:
                    missing.append(f"classification.{req}")
                elif req in ("domain", "difficulty") and isinstance(sec_obj[req], dict):
                    missing.extend(f"classification.{req}.{s}" for s in ("instructions", "criteria") if s not in sec_obj[req])
                    criteria = sec_obj[req].get("criteria")
                    if isinstance(criteria, dict):
                        keys = DOMAIN_CRITERIA_KEYS if req == "domain" else DIFFICULTY_CRITERIA_KEYS
                        missing.extend(f"classification.{req}.criteria.{key}" for key in keys if key not in criteria)
        elif sec == "riskFacts" and not sec_obj:
            missing.append("riskFacts")
        elif sec == "roundTiers":
            missing.extend(f"roundTiers.{k}" for k in ROUND_TIER_KEYS if k not in sec_obj)
        elif sec == "jev":
            for req in JEV_KEYS:
                if req not in sec_obj:
                    missing.append(f"jev.{req}")
                elif req == "keyFile" and isinstance(sec_obj[req], dict):
                    missing.extend(f"jev.keyFile.{s}" for s in ("path", "field") if s not in sec_obj[req])
        elif sec == "availabilityFallback":
            missing.extend(f"availabilityFallback.{k}" for k in FALLBACK_KEYS if k not in sec_obj)
    return tuple(missing)


def _validate_options(options, path, errors):
    if not isinstance(options, dict):
        _err(errors, path, "expected-object")
        return
    for k in sorted(options):
        if not _nonempty_str(k):
            _err(errors, path, "invalid-option-key")
        elif not isinstance(options[k], (str, bool)):
            _err(errors, f"{path}.{k}", "invalid-option-value")


def _validate_roles(roles, path, allowed_roles, errors, worker_only=False):
    if not isinstance(roles, list):
        _err(errors, path, "expected-list")
    elif worker_only and roles != ["worker"]:
        _err(errors, path, "pool-selection-requires-worker-only-role")
    elif not worker_only and not roles:
        _err(errors, path, "empty-roles")
    elif not worker_only:
        seen = set()
        for idx, role in enumerate(roles):
            if not isinstance(role, str) or role not in allowed_roles:
                _err(errors, f"{path}[{idx}]", "invalid-role")
            elif role in seen:
                _err(errors, f"{path}[{idx}]", "duplicate-role")
            else:
                seen.add(role)


def _validate_selection(obj, path, kind, pools, pool_members, allowed_roles, errors):
    error_count = len(errors)
    if not isinstance(obj, dict):
        _err(errors, path, "expected-object")
        return None
    has_direct, has_pool = "providerInstanceId" in obj, "providerPool" in obj
    if has_direct == has_pool:
        _err(errors, path, "disjoint-selection-keys" if has_direct else "missing-selection-target")
        return None
    if kind == "pool-fixed" and has_direct:
        _err(errors, f"{path}.providerInstanceId", "expected-pool-selection")
        return None
    if kind in ("master", "challenging") and has_pool:
        _err(errors, f"{path}.providerPool", "expected-direct-selection" if kind == "master" else "pool-selection-not-allowed-at-challenging-tier")
        return None
    if has_pool:
        if kind == "pool-fixed" and "roles" in obj:
            _err(errors, f"{path}.roles", "roles-not-allowed")
        _check_keys(obj, path, ("providerPool", "model", "options"), () if kind == "pool-fixed" else ("roles",), errors)
        pool_name = obj.get("providerPool")
        if not _nonempty_str(pool_name):
            _err(errors, f"{path}.providerPool", "invalid-string")
        elif pool_name not in pools:
            _err(errors, f"{path}.providerPool", "unknown-provider-pool")
        _check_str_field(obj, "model", path, errors)
        if "options" in obj:
            _validate_options(obj["options"], f"{path}.options", errors)
        if kind != "pool-fixed":
            if "roles" not in obj:
                _err(errors, f"{path}.roles", "pool-selection-requires-worker-only-role")
            else:
                _validate_roles(obj["roles"], f"{path}.roles", allowed_roles, errors, worker_only=True)
        if len(errors) != error_count:
            return None
        return ("pool", pool_name, obj.get("model"), canonical_json(obj["options"]) if isinstance(obj.get("options"), dict) else b"")

    _check_keys(obj, path, ("providerInstanceId", "model", "options"), ("roles",), errors)
    inst_id = obj.get("providerInstanceId")
    if not _nonempty_str(inst_id):
        _err(errors, f"{path}.providerInstanceId", "invalid-string")
    elif inst_id in pool_members:
        _err(errors, f"{path}.providerInstanceId", "direct-selection-names-pool-member")
    _check_str_field(obj, "model", path, errors)
    if "options" in obj:
        _validate_options(obj["options"], f"{path}.options", errors)
    if "roles" in obj:
        _validate_roles(obj["roles"], f"{path}.roles", allowed_roles, errors, worker_only=False)
    if len(errors) != error_count:
        return None
    return ("direct", inst_id, obj.get("model"), canonical_json(obj["options"]) if isinstance(obj.get("options"), dict) else b"")


def _selection_summary(obj):
    key = "providerPool" if "providerPool" in obj else "providerInstanceId"
    return {key: obj[key], "model": obj["model"], "options": thaw_value(obj["options"])}


def validate_config_data(raw):
    errors = []
    if not isinstance(raw, dict):
        raise ConfigValidationError([{"path": "$", "rule": "expected-object"}])
    version = raw.get("version")
    if _int_not_bool(version) and version == 1:
        missing = _collect_missing_v2_sections(raw)
        errors.append({"path": "version", "rule": "unsupported-version-1", "missingSections": list(missing)})
        errors.extend({"path": s, "rule": "missing-version-2-section"} for s in VERSION_2_NEW_SECTIONS if s not in raw)
        errors.extend({"path": k, "rule": "unknown-key"} for k in sorted(raw) if k not in TOP_LEVEL_CONFIG_KEYS)
        raise ConfigValidationError(errors, missing_sections=missing)

    _check_keys(raw, "$", TOP_LEVEL_CONFIG_KEYS, (), errors)
    if not _int_not_bool(version) or version != 2:
        _err(errors, "version", "invalid-version")

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

    allowed_roles = set(FALLBACK_ROLES)
    raw_fb = raw.get("availabilityFallback")
    if "availabilityFallback" in raw and _check_keys(raw_fb, "availabilityFallback", FALLBACK_KEYS, (), errors):
        if "roles" in raw_fb and not isinstance(raw_fb["roles"], list):
            _err(errors, "availabilityFallback.roles", "expected-list")
        elif "roles" in raw_fb and raw_fb["roles"] != list(FALLBACK_ROLES):
            _err(errors, "availabilityFallback.roles", "invalid-fallback-roles")
        if "manager" in raw_fb and raw_fb["manager"] != "owner-choice":
            _err(errors, "availabilityFallback.manager", "invalid-manager-fallback")
        for flag in ("recordReason", "reportReason"):
            if flag in raw_fb and raw_fb[flag] is not True:
                _err(errors, f"availabilityFallback.{flag}", "expected-true")

    for slot, kind in (("master", "master"), ("easyWorker", "pool-fixed"), ("textHelper", "pool-fixed")):
        if slot in raw:
            _validate_selection(raw[slot], slot, kind, pools, pool_members, allowed_roles, errors)

    if "modelPreferences" in raw and _check_keys(raw["modelPreferences"], "modelPreferences", PREFERENCE_DOMAINS, (), errors):
        for dom in PREFERENCE_DOMAINS:
            if dom in raw["modelPreferences"]:
                dom_obj, dom_path = raw["modelPreferences"][dom], f"modelPreferences.{dom}"
                if _check_keys(dom_obj, dom_path, PREFERENCE_TIERS, (), errors):
                    for tier in PREFERENCE_TIERS:
                        if tier in dom_obj:
                            _validate_selection(dom_obj[tier], f"{dom_path}.{tier}", tier, pools, pool_members, allowed_roles, errors)

    if "classification" in raw and _check_keys(raw["classification"], "classification", ("ruleVersion", "domain", "difficulty"), (), errors):
        raw_cls = raw["classification"]
        _check_str_field(raw_cls, "ruleVersion", "classification", errors)
        for q_name, exp_keys in (("domain", DOMAIN_CRITERIA_KEYS), ("difficulty", DIFFICULTY_CRITERIA_KEYS)):
            if q_name in raw_cls:
                q_obj, q_path = raw_cls[q_name], f"classification.{q_name}"
                if _check_keys(q_obj, q_path, ("instructions", "criteria"), (), errors):
                    _check_str_field(q_obj, "instructions", q_path, errors)
                    if "criteria" in q_obj and _check_keys(q_obj["criteria"], f"{q_path}.criteria", exp_keys, (), errors):
                        for ck in exp_keys:
                            _check_str_field(q_obj["criteria"], ck, f"{q_path}.criteria", errors)

    if "riskFacts" in raw:
        raw_facts = raw["riskFacts"]
        if not isinstance(raw_facts, dict) or not raw_facts:
            _err(errors, "riskFacts", "expected-object" if not isinstance(raw_facts, dict) else "empty-object")
        else:
            for fid in sorted(raw_facts):
                fpath, fobj = f"riskFacts.{fid}", raw_facts[fid]
                if not isinstance(fid, str) or not FACT_ID_RE.fullmatch(fid):
                    _err(errors, fpath, "invalid-fact-id")
                if _check_keys(fobj, fpath, ("description", "floor"), (), errors):
                    _check_str_field(fobj, "description", fpath, errors)
                    if "floor" in fobj and fobj["floor"] not in ("medium", "challenging"):
                        _err(errors, f"{fpath}.floor", "invalid-floor")

    if "roundTiers" in raw and _check_keys(raw["roundTiers"], "roundTiers", ROUND_TIER_KEYS, (), errors):
        raw_rt = raw["roundTiers"]
        if "corrections" in raw_rt and raw_rt["corrections"] not in ("easy", "medium", "challenging"):
            _err(errors, "roundTiers.corrections", "invalid-tier")
        for rk in ("correctionsReview", "documentReview"):
            if rk in raw_rt and raw_rt[rk] not in ("medium", "challenging"):
                _err(errors, f"roundTiers.{rk}", "invalid-reviewer-tier")

    if "jev" in raw and _check_keys(raw["jev"], "jev", JEV_KEYS, (), errors):
        raw_jev = raw["jev"]
        if "provider" in raw_jev and raw_jev["provider"] != "openrouter":
            _err(errors, "jev.provider", "invalid-jev-provider")
        _check_str_field(raw_jev, "model", "jev", errors)
        if "minConfidence" in raw_jev and (not _num_not_bool(raw_jev["minConfidence"]) or not (0 < raw_jev["minConfidence"] <= 1)):
            _err(errors, "jev.minConfidence", "invalid-min-confidence")
        if "maxContextRetries" in raw_jev and (not _int_not_bool(raw_jev["maxContextRetries"]) or raw_jev["maxContextRetries"] not in (0, 1)):
            _err(errors, "jev.maxContextRetries", "invalid-max-context-retries")
        if "uncertainResult" in raw_jev and raw_jev["uncertainResult"] != "manager-judgment":
            _err(errors, "jev.uncertainResult", "invalid-uncertain-result")
        if "timeoutSeconds" in raw_jev and (not _int_not_bool(raw_jev["timeoutSeconds"]) or not (1 <= raw_jev["timeoutSeconds"] <= 120)):
            _err(errors, "jev.timeoutSeconds", "invalid-timeout-seconds")
        if "keyFile" in raw_jev and _check_keys(raw_jev["keyFile"], "jev.keyFile", ("path", "field"), (), errors):
            kf = raw_jev["keyFile"]
            if "path" in kf and (not _nonempty_str(kf["path"]) or not kf["path"].startswith("/")):
                _err(errors, "jev.keyFile.path", "expected-absolute-path")
            _check_str_field(kf, "field", "jev.keyFile", errors)

    notices = []
    if isinstance(raw_fb, dict) and "chains" in raw_fb:
        chains = raw_fb["chains"]
        if not isinstance(chains, list):
            _err(errors, "availabilityFallback.chains", "expected-list")
        else:
            seen_sources, valid_pairs = set(), []
            for idx, chain in enumerate(chains):
                c_path = f"availabilityFallback.chains[{idx}]"
                if not _check_keys(chain, c_path, ("tier", "from", "to"), (), errors):
                    continue
                tier = chain.get("tier")
                if "tier" in chain and tier not in ("medium", "challenging"):
                    _err(errors, f"{c_path}.tier", "invalid-chain-tier")
                sel_kind = tier if tier in ("medium", "challenging") else "medium"
                from_id = _validate_selection(chain["from"], f"{c_path}.from", sel_kind, pools, pool_members, allowed_roles, errors) if "from" in chain else None
                if tier in ("medium", "challenging") and from_id is not None:
                    if (tier, from_id) in seen_sources:
                        _err(errors, f"{c_path}.from", "duplicate-chain-source")
                    else:
                        seen_sources.add((tier, from_id))
                to_id = None
                if "to" in chain:
                    to_list = chain["to"]
                    if not isinstance(to_list, list) or len(to_list) != 1:
                        _err(errors, f"{c_path}.to", "expected-list" if not isinstance(to_list, list) else "chain-requires-single-target")
                    else:
                        to_id = _validate_selection(to_list[0], f"{c_path}.to[0]", sel_kind, pools, pool_members, allowed_roles, errors)
                if tier in ("medium", "challenging") and from_id is not None and to_id is not None:
                    if from_id == to_id:
                        _err(errors, f"{c_path}.to[0]", "chain-target-equals-source")
                    else:
                        valid_pairs.append((idx, tier, from_id, to_id, chain["from"], chain["to"][0]))
            pair_set = {(t, f_id, t_id) for _, t, f_id, t_id, _, _ in valid_pairs}
            for idx, tier, f_id, t_id, f_obj, t_obj in valid_pairs:
                if (tier, t_id, f_id) not in pair_set:
                    notices.append({
                        "kind": "missing-reverse-chain", "path": f"availabilityFallback.chains[{idx}]",
                        "tier": tier, "from": _selection_summary(f_obj), "to": _selection_summary(t_obj),
                    })
    if errors:
        raise ConfigValidationError(errors)
    return freeze_value(raw), tuple(freeze_value(n) for n in notices)


def routing_policy_projection(config_data):
    """Project exact config fields that govern classification and risk-floor freshness."""
    jev = config_data["jev"]
    return {
        "classification": thaw_value(config_data["classification"]),
        "riskFacts": thaw_value(config_data["riskFacts"]),
        "jev": {k: jev[k] for k in ("provider", "model", "minConfidence", "maxContextRetries", "uncertainResult")},
    }


def compute_policy_digest(config_data):
    return "sha256:" + hashlib.sha256(canonical_json(routing_policy_projection(config_data))).hexdigest()


def validate_quota_state_data(raw, config_data=None):
    errors = []
    if not isinstance(raw, dict):
        raise ConfigValidationError([{"path": "$", "rule": "expected-object"}])
    _check_keys(raw, "$", TOP_LEVEL_STATE_KEYS, (), errors)
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


def load_config(path=None, *, local=None):
    """Read, validate, and return deeply read-only routing config and digests without caching."""
    target = Path(path) if path is not None else (Path(local) if local is not None else local_dir()) / DEFAULT_CONFIG_NAME
    raw, digest = _read_bounded_json(target, "config")
    frozen, notices = validate_config_data(raw)
    return LoadedConfig(data=frozen, digest=digest, policy_digest=compute_policy_digest(frozen), notices=notices)


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
    errors, missing_sections, loaded_cfg, loaded_state = [], (), None, None
    try:
        loaded_cfg = load_config(config_path)
    except ConfigValidationError as exc:
        errors.extend(thaw_value(e) for e in exc.errors)
        missing_sections = exc.missing_sections
    try:
        loaded_state = load_quota_state(state_path, config=loaded_cfg.data if loaded_cfg is not None else None)
    except ConfigValidationError as exc:
        errors.extend(thaw_value(e) for e in exc.errors)
    if errors:
        payload = {"valid": False, "errors": errors}
        if missing_sections:
            payload["missingSections"] = list(missing_sections)
        return Outcome(payload, ExitCode.INVALID_CONFIG)
    return {
        "digest": loaded_cfg.digest, "policyDigest": loaded_cfg.policy_digest,
        "stateDigest": loaded_state.digest,
        "ruleVersion": loaded_cfg.data["classification"]["ruleVersion"],
        "riskFacts": {fid: info["floor"] for fid, info in loaded_cfg.data["riskFacts"].items()},
        "roundTiers": thaw_value(loaded_cfg.data["roundTiers"]),
        "notices": [thaw_value(n) for n in loaded_cfg.notices],
    }


def register(registry):
    parser = registry.group("config").add_parser("check")
    parser.add_argument("--config", default=None)
    parser.add_argument("--state", default=None)
    parser.set_defaults(handler=check_config_command)
