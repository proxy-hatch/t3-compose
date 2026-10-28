"""Bounded Decisions API classification; no route or execution-model selection."""
from contextlib import suppress
import hashlib
import json
import os
import re
import ssl
import stat
import urllib.error
import urllib.request

from . import cli, config, identifier_list, jsonio, records, schema

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
INPUT_LIMIT = 16 * 1024
RESPONSE_LIMIT = 256 * 1024
KEY_LIMIT = 4096
QUESTIONS = ("domain", "difficulty")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        # Reject before urllib parses Location; retain the response for cleanup.
        raise urllib.error.HTTPError(req.full_url, code, "Jev redirect refused", headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def open_http(request, *, timeout):
    """Injectable HTTP seam. Each call has its own verified, nonredirecting opener."""
    try:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        with suppress(Exception):
            error.close()
    except Exception:
        pass
    raise OSError("Jev transport failed") from None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _decode(raw, limit):
    if len(raw) > limit:
        raise ValueError("JSON byte limit exceeded")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    jsonio.canonical_json(value)  # Reject nonfinite numbers and invalid Unicode everywhere.
    return value


def _key(settings):
    try:
        key = os.environ.get("OPENROUTER_API_KEY")
        if key is None:
            descriptor = os.open(settings["keyFile"]["path"], os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("not a regular key file")
                value = _decode(stream.read(INPUT_LIMIT + 1), INPUT_LIMIT)
            key = value[settings["keyFile"]["field"]]
        if (not isinstance(key, str) or not 1 <= len(key) <= KEY_LIMIT or
                not re.fullmatch(r"[A-Za-z0-9._~+/-]+", key)):
            raise ValueError("invalid credential")
        return key
    except Exception:
        raise cli.CommandError("invalid Jev credential setup", cli.ExitCode.INVALID_CONFIG) from None


def _text(value):
    """Include decoded text as well as wire JSON so escapes cannot hide identifiers."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _text(item)
    elif isinstance(value, list):
        for item in value:
            yield from _text(item)


def _sensitive(value, secrets):
    text = jsonio.canonical_json(value).decode("utf-8") + "\n" + "\n".join(_text(value))
    return any(secret in text for secret in secrets)


def _screen(local, body, metadata, secrets):
    text = jsonio.canonical_json(body).decode("utf-8")
    text += "\n" + "\n".join(_text(body))
    # Metadata is persisted too, though it is not sent to the service.
    if _sensitive(body, secrets) or _sensitive(metadata, secrets):
        raise cli.CommandError("credential found in classification input")
    patterns = identifier_list.load_identifiers(local)
    matches = identifier_list.match_identifiers(patterns, text)
    if matches:
        raise cli.CommandError("classification input matches identifier list lines: " +
                               ", ".join(map(str, matches)))


def _information(value):
    if isinstance(value, dict):
        return any(_information(v) for v in value.values())
    if isinstance(value, list):
        return any(_information(v) for v in value)
    return bool(value.strip()) if isinstance(value, str) else value is not None


def _retry(events, task, context, policy, maximum):
    """Consume attempts by persisted input, not by a caller-supplied retry counter."""
    inputs = [e for e in events if e["kind"] == "routing-input" and
              e["data"]["policyDigest"] == policy and
              jsonio.canonical_json(e["data"]["task"]) == jsonio.canonical_json(task)]
    if not inputs:
        if context is not None:
            raise cli.CommandError("retry context requires a matching prior classification")
        return False
    latest = inputs[-1]
    classifications = [e for e in events if e["kind"] == "routing-classification" and
                       e["data"]["inputRef"] == latest["seq"]]
    if not classifications:
        raise cli.CommandError("routing input has no classification; reconcile records")
    prior = classifications[-1]
    allowed = (len(inputs) == 1 and maximum == 1 and
               prior["data"]["outcome"] == "retry-with-context" and context is not None and
               context.get("priorClassificationRef") == prior["seq"] and
               _information({k: v for k, v in context.items() if k != "priorClassificationRef"}))
    if allowed:
        return True
    return cli.Outcome({**prior["data"], "classificationRef": prior["seq"],
                        "outcome": "manager-judgment",
                        "reason": "retry refused; use eligible context once or obtain manager judgment"},
                       cli.ExitCode.JUDGMENT_NEEDED)


def _request(body, key, timeout):
    try:
        request = urllib.request.Request(ENDPOINT, data=jsonio.canonical_json(body), method="POST",
                                         headers={"Authorization": "Bearer " + key,
                                                  "Content-Type": "application/json"})
        with open_http(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise ValueError("unsuccessful HTTP status")
            raw = response.read(RESPONSE_LIMIT + 1)
        return raw
    except urllib.error.HTTPError as error:
        with suppress(Exception):
            error.close()
    except Exception:
        pass
    raise ValueError("Jev request failed") from None


def _answer(raw, data, secrets, settings, retried):
    """Whitelist response fields and validate with the same schema as durable records."""
    try:
        value = _decode(raw, RESPONSE_LIMIT)
        if _sensitive(value, secrets):
            raise ValueError("credential echo")
        for field in ("model", "id"):
            if not isinstance(value[field], str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:/~+-]{0,255}", value[field]):
                raise ValueError("invalid service identity")
        candidate = {**data, "snapshotModel": value["model"], "responseId": value["id"],
                     "answers": value["answers"]}
        schema.validate_event_data("routing-classification", candidate)
    except Exception:
        return {**data, "reason": "invalid Jev response"}
    answers = candidate["answers"]
    # The schema permits explicit unknowns only for failed outcomes, never here.
    if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
        return {**data, "reason": "invalid Jev response"}
    if answers["domain"]["choice"] == "MIXED_OR_UNCLEAR":
        outcome, reason = "manager-judgment", "mixed or unclear domain"
    elif (answers["difficulty"]["choice"] == "INSUFFICIENT_CONTEXT" or
          any(answers[q]["confidence"] < settings["minConfidence"] for q in QUESTIONS)):
        outcome = "retry-with-context" if settings["maxContextRetries"] and not retried else "manager-judgment"
        reason = "insufficient context or confidence"
    else:
        outcome, reason = "accepted", "both answers meet classification policy"
    return {**candidate, "outcome": outcome, "reason": reason}


def classify(local, repo, work_id, task, context=None):
    """Classify parsed input once; also used by the EM-only synthetic live check."""
    try:
        return _classify(local, repo, work_id, task, context)
    except cli.CommandError:
        raise
    except Exception:
        raise cli.CommandError("classification input or records invalid; reconcile before retry") from None


def _classify(local, repo, work_id, task, context):
    try:
        loaded = config.load_config(local=local)
    except Exception:
        raise cli.CommandError("invalid routing configuration", cli.ExitCode.INVALID_CONFIG) from None
    cfg = config.thaw_value(loaded.data)
    schema.validate_routing_input({"task": task, "context": context})
    if any(fact not in cfg["riskFacts"] for fact in task["riskFacts"]):
        raise cli.CommandError("unconfigured task risk fact")
    key = _key(cfg["jev"])
    secrets = (key, cfg["jev"]["keyFile"]["path"])
    work = records.load_work(local, repo, work_id)
    writer = work["effectiveWork"]["writer"]
    questions = {q: {"type": "choice", **cfg["classification"][q]} for q in QUESTIONS}
    body = {"model": cfg["jev"]["model"], "state": {"task": task, "context": context},
            "questions": questions}
    criteria = {q: {**questions[q]["criteria"], "instructions": questions[q]["instructions"]}
                for q in QUESTIONS}
    unknown = {"unknown": "invalid Jev response"}
    data = {"ruleVersion": cfg["classification"]["ruleVersion"], "criteria": criteria,
            "criteriaDigest": "sha256:" + hashlib.sha256(jsonio.canonical_json(criteria)).hexdigest(),
            "riskFacts": task["riskFacts"], "configuredModel": cfg["jev"]["model"],
            "snapshotModel": unknown, "responseId": unknown, "answers": unknown,
            "outcome": "manager-judgment", "reason": "Jev request failed"}
    try:
        # Validate local metadata before any side effect. This reference is only
        # a shape check; the recorder supplies the real provenance below.
        schema.validate_event_data("routing-classification", {
            **data, "inputRef": 1, "inputDigest": "sha256:" + "0" * 64,
            "policyDigest": loaded.policy_digest})
    except Exception:
        raise cli.CommandError("invalid classification record configuration",
                               cli.ExitCode.INVALID_CONFIG) from None
    _screen(local, body, {"record": data, "writer": writer, "repo": repo, "work": work_id}, secrets)
    retried = _retry(work["events"], task, context, loaded.policy_digest, cfg["jev"]["maxContextRetries"])
    if isinstance(retried, cli.Outcome):
        if _sensitive(retried.data, secrets):
            raise cli.CommandError("credential found in prior classification")
        return retried
    reference = records.record_routing_input(local, repo, work_id, task=task, context=context,
                                             policy_digest=loaded.policy_digest, writer=writer)
    data.update(reference)
    try:
        raw = _request(body, key, cfg["jev"]["timeoutSeconds"])
    except Exception:
        pass  # Only the fixed public failure reason is persisted.
    else:
        data = _answer(raw, data, secrets, cfg["jev"], retried)
    event = records.append_event(local, repo, work_id, "routing-classification", data, writer=writer)
    return cli.Outcome({**data, "classificationRef": event["seq"]},
                       cli.ExitCode.SUCCESS if data["outcome"] == "accepted" else cli.ExitCode.JUDGMENT_NEEDED)


def command(args):
    try:
        task = jsonio.read_json(args.input, max_bytes=INPUT_LIMIT)
        context = jsonio.read_json(args.context, max_bytes=INPUT_LIMIT) if args.context else None
        if args.context is not None and (not isinstance(context, dict) or not context):
            raise ValueError("context file must hold a nonempty object")
    except Exception:
        raise cli.CommandError("invalid or oversized classification input file") from None
    return classify(args.local_dir, args.repo, args.work, task, context)


def register(registry):
    parser = registry.group("route").add_parser("classify", help="Classify bounded task requirements")
    for option in ("repo", "work", "input"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--context")
    parser.set_defaults(handler=command)
