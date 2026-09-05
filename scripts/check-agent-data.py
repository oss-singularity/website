#!/usr/bin/env python3
"""Validate the public discovery contract using only the Python standard library."""

from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN = "https://oss-singularity.io"
RUNTIME_DISCOVERY_URLS = {ORIGIN + "/api/v1"}
CATEGORIES = {"coding", "personal", "frameworks", "local", "protocols"}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SCHEMA_KEYS = {
    "$schema", "$id", "title", "description", "type", "const", "required",
    "properties", "additionalProperties", "minLength", "maxLength", "format", "pattern",
    "enum", "items", "minItems", "maxItems", "uniqueItems",
}


class ContractError(ValueError):
    """A public data contract was violated."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ContractError(f"non-JSON numeric constant: {value}")


def read_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    require(raw.endswith("\n") and "\r" not in raw, f"{path}: expected LF-terminated UTF-8")
    value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    require(isinstance(value, dict), f"{path}: expected an object")
    return value


def calendar_date(value: object, label: str) -> date:
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)),
            f"{label}: expected YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{label}: invalid calendar date") from error
    require(parsed <= datetime.now(timezone.utc).date(), f"{label}: editorial date is in the future (UTC)")
    return parsed


def text_field(value: object, label: str, maximum: int = 500) -> None:
    require(isinstance(value, str) and bool(value.strip()), f"{label}: expected nonempty text")
    require(len(value) <= maximum, f"{label}: exceeds {maximum} characters")
    require(not any(ord(char) < 32 for char in value), f"{label}: contains a control character")


def fields(value: object, expected: set[str], label: str) -> None:
    require(isinstance(value, dict), f"{label}: expected an object")
    require(set(value) == expected,
            f"{label}: expected fields {sorted(expected)}, got {sorted(value)}")


def https_url(value: object, label: str) -> None:
    text_field(value, label, 2048)
    require(not any(char.isspace() for char in value) and "\\" not in value,
            f"{label}: URL contains whitespace or backslashes")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ContractError(f"{label}: malformed URL") from error
    require(parsed.scheme == "https" and bool(parsed.hostname), f"{label}: expected absolute HTTPS URL")
    require(parsed.username is None and parsed.password is None, f"{label}: URL must not contain credentials")
    require(port in {None, 443}, f"{label}: URL must use the standard HTTPS port")
    host = parsed.hostname.lower().rstrip(".")
    require("." in host and not host.endswith((".local", ".localhost", ".internal")),
            f"{label}: expected a public hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    require(address is None or address.is_global, f"{label}: private IP address is not a public source")


def validate_manifest(value: object, schema: dict, label: str = "manifest") -> None:
    """Check the exact JSON Schema vocabulary used by our published discovery schemas."""
    require(not set(schema) - SCHEMA_KEYS, f"{label}: unsupported schema vocabulary")
    if "const" in schema:
        expected = schema["const"]
        require(type(value) is type(expected) and value == expected,
                f"{label}: must equal {expected!r}")
    if "enum" in schema:
        require(any(type(value) is type(expected) and value == expected for expected in schema["enum"]),
                f"{label}: unsupported value")
    if schema.get("type") == "object":
        require(isinstance(value, dict), f"{label}: expected an object")
        properties = schema.get("properties", {})
        require(set(schema.get("required", [])) <= set(value), f"{label}: missing required fields")
        if schema.get("additionalProperties") is False:
            require(set(value) <= set(properties), f"{label}: undeclared fields")
        for name, child in properties.items():
            if name in value:
                validate_manifest(value[name], child, f"{label}.{name}")
    elif schema.get("type") == "string":
        require(isinstance(value, str), f"{label}: expected a string")
        require(len(value) >= schema.get("minLength", 0), f"{label}: text is too short")
        require(len(value) <= schema.get("maxLength", 2048), f"{label}: text is too long")
        require(bool(value.strip()), f"{label}: expected nonempty text")
        if "pattern" in schema:
            require(bool(re.search(schema["pattern"], value)), f"{label}: invalid text format")
        if schema.get("format") == "date":
            calendar_date(value, label)
    elif schema.get("type") == "array":
        require(isinstance(value, list), f"{label}: expected an array")
        require(schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 5000),
                f"{label}: invalid item count")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            require(len(serialized) == len(set(serialized)), f"{label}: duplicate items")
        for index, item in enumerate(value):
            validate_manifest(item, schema["items"], f"{label}[{index}]")
    else:
        require("type" not in schema, f"{label}: unsupported schema type")


def dataset(value: dict, collection: str, version: str = "1.0") -> tuple[list, date]:
    fields(value, {"schema_version", "updated", collection}, collection)
    require(value["schema_version"] == version, f"{collection}: unsupported schema version")
    updated = calendar_date(value["updated"], f"{collection}.updated")
    records = value[collection]
    require(isinstance(records, list) and 0 < len(records) <= 5000,
            f"{collection}: expected a nonempty array with at most 5000 records")
    return records, updated


def identifier(value: object, seen: set[str], label: str) -> None:
    require(isinstance(value, str) and len(value) <= 100 and bool(SLUG.fullmatch(value)),
            f"{label}: expected a lowercase slug")
    require(value not in seen, f"{label}: duplicate identifier {value!r}")
    seen.add(value)


def string_array(value: object, label: str, maximum: int = 20) -> None:
    require(isinstance(value, list) and 0 < len(value) <= maximum,
            f"{label}: expected 1 to {maximum} text items")
    for index, item in enumerate(value):
        text_field(item, f"{label}[{index}]")
    require(len(value) == len(set(value)), f"{label}: duplicate text items")


def validate_atlas(value: dict) -> int:
    entries, updated = dataset(value, "entries", "1.1")
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"atlas.entries[{index}]"
        fields(entry, {"id", "name", "category", "summary", "use_case", "website",
                       "source_url", "license", "reviewed", "tags"}, label)
        identifier(entry["id"], seen, f"{label}.id")
        require(isinstance(entry["category"], str) and entry["category"] in CATEGORIES,
                f"{label}: unsupported category")
        for key in ("name", "summary", "use_case", "license"):
            text_field(entry[key], f"{label}.{key}")
        for key in ("website", "source_url"):
            https_url(entry[key], f"{label}.{key}")
        reviewed = calendar_date(entry["reviewed"], f"{label}.reviewed")
        require(reviewed <= updated, f"{label}: reviewed date is newer than dataset update")
        string_array(entry["tags"], f"{label}.tags", 12)
    return len(entries)


def validate_missions(value: dict) -> int:
    missions, _ = dataset(value, "missions")
    seen: set[str] = set()
    for index, mission in enumerate(missions):
        label = f"missions[{index}]"
        fields(mission, {"id", "title", "summary", "goal", "deliverable", "constraints", "acceptance"}, label)
        identifier(mission["id"], seen, f"{label}.id")
        for key in ("title", "summary", "goal", "deliverable"):
            text_field(mission[key], f"{label}.{key}", 1000)
        for key in ("constraints", "acceptance"):
            string_array(mission[key], f"{label}.{key}")
    return len(missions)


def local_target(root: Path, url: str) -> Path:
    https_url(url, "local reference")
    parsed = urlsplit(url)
    require(parsed.scheme + "://" + parsed.netloc == ORIGIN,
            f"local reference uses another origin: {url}")
    require(not parsed.query and not parsed.fragment, f"local reference must not use a query or fragment: {url}")
    require(parsed.path.startswith("/") and "%" not in parsed.path and "//" not in parsed.path,
            f"local reference is not a normalized absolute path: {url}")
    require(not {".", ".."} & set(parsed.path.split("/")), f"local reference contains traversal: {url}")
    target = root / parsed.path.lstrip("/")
    if parsed.path.endswith("/"):
        target /= "index.html"
    require(target.resolve().is_relative_to(root.resolve()), f"local reference escapes publication tree: {url}")
    require(target.is_file(), f"local reference is missing: {url}")
    return target


def validate_openapi(spec: dict) -> None:
    """Guard the published public API surface and receipt security contract."""
    require(spec.get("openapi") == "3.1.0", "Commons must publish OpenAPI 3.1.0")
    require(spec.get("servers") == [{"url": ORIGIN, "description": "Canonical OSS Singularity origin"}],
            "Commons OpenAPI must target only the canonical origin")
    operations = {
        "/api/v1": ("get",), "/api/v1/activity": ("get",), "/api/v1/missions": ("get",), "/api/v1/missions/{id}": ("get",),
        "/api/v1/contributions": ("get",), "/api/v1/reviews": ("get",),
        "/api/v1/proposals": ("post",), "/api/v1/proposals/{id}": ("get",),
        "/api/v1/identity-challenges": ("post",), "/api/v1/identities": ("post",),
        "/api/v1/identities/{id}": ("get",), "/api/v1/participations": ("get", "post"),
        "/api/v1/participations/mine": ("get",), "/api/v1/participations/{id}": ("get", "patch"),
    }
    require(set(spec.get("paths", {})) == set(operations), "Commons OpenAPI public route set differs")
    require(spec.get("security") == [], "Commons public reads must not claim account authentication")
    scopes = {
        ("/api/v1/proposals/{id}", "get"): [{"ReceiptBearer": []}],
        ("/api/v1/proposals", "post"): [{}, {"IdentityBearer": []}],
        ("/api/v1/identities", "post"): [{"ChallengeBearer": []}],
        ("/api/v1/participations", "post"): [{"IdentityBearer": []}],
        ("/api/v1/participations/mine", "get"): [{"IdentityBearer": []}],
        ("/api/v1/participations/{id}", "get"): [{"ReceiptBearer": []}],
        ("/api/v1/participations/{id}", "patch"): [{"IdentityBearer": []}],
    }
    names = set()
    for path, methods in operations.items():
        require(set(spec["paths"][path]) == set(methods), f"Commons OpenAPI methods differ: {path}")
        for method in methods:
            operation = spec["paths"][path][method]
            name = operation.get("operationId")
            require(isinstance(name, str) and name not in names, f"Commons OpenAPI operationId missing or repeated: {path}")
            names.add(name)
            require(operation.get("security", []) == scopes.get((path, method), []),
                    f"Commons OpenAPI credential scope differs: {method} {path}")
    components = spec.get("components", {})
    bearer = components.get("securitySchemes", {}).get("ReceiptBearer", {})
    require(bearer.get("type") == "http" and bearer.get("scheme") == "bearer",
            "Commons receipt must use HTTP Bearer authentication")
    schemas = components.get("schemas", {})
    request = schemas.get("ProposalRequest", {})
    require(request.get("required") == ["kind", "title", "summary"] and request.get("additionalProperties") is False,
            "Commons proposal request contract differs")
    require(set(request.get("properties", {})) == {"kind", "title", "summary", "url", "mission_id", "target_id", "score"},
            "Commons proposal request declares unsupported fields")
    receipt = schemas.get("ProposalReceipt", {})
    require(set(receipt.get("required", [])) == {"id", "status", "poll_url", "receipt_token"},
            "Commons submission receipt fields differ")
    require(receipt.get("properties", {}).get("status") == {"const": "pending"},
            "Commons submissions must begin pending")
    require("receipt_hash" not in schemas.get("Proposal", {}).get("properties", {}),
            "Commons OpenAPI must not expose stored receipt hashes")
    for name in ("IdentityBearer", "ChallengeBearer"):
        credential = components.get("securitySchemes", {}).get(name, {})
        require(credential.get("type") == "http" and credential.get("scheme") == "bearer",
                f"Commons {name} must use a distinct HTTP Bearer scope")
    require("challenge_token" in schemas.get("IdentityChallenge", {}).get("required", []),
            "Commons enrollment needs a private challenge token")
    require(set(schemas.get("ChallengeProof", {}).get("properties", {})) == {"network", "challenge_id", "nonce"},
            "Commons public proof must not contain a private token")
    require(set(schemas.get("Identity", {}).get("properties", {})).isdisjoint({"token_hash", "api_token", "challenge_token"}),
            "Commons public identities must not expose credentials")

    participation = schemas.get("ParticipationRequest", {})
    require(participation.get("additionalProperties") is False and
            set(participation.get("properties", {})) == {"mission_id", "intent", "participant_type", "collaboration", "title", "summary", "url"},
            "Participation request must not accept client-supplied identity or unsupported fields")
    require(set(participation.get("required", [])) == {"mission_id", "intent", "participant_type", "collaboration", "title", "summary"},
            "Participation requires an explicit mission, intent and collaboration terms")
    require(set(schemas.get("Participation", {}).get("properties", {})).isdisjoint({"receipt_hash", "receipt_token", "token_hash", "api_token"}),
            "Participation cards must not expose credentials")
    require(set(schemas.get("ParticipationReceipt", {}).get("required", [])) == {"id", "status", "state", "expires_at", "poll_url", "receipt_token"},
            "Participation receipt must expose pending state and expiry")
    state_request = schemas.get("ParticipationStateRequest", {})
    require(state_request.get("additionalProperties") is False and set(state_request.get("properties", {})) == {"state"},
            "Participation owner changes must not edit content or moderation")
    require(set(state_request.get("properties", {}).get("state", {}).get("enum", [])) == {"closed", "withdrawn"},
            "Participation owner changes must not reopen or publish cards")

    def references(value: object) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                require(isinstance(reference, str) and reference.startswith("#/components/"),
                        "Commons OpenAPI references must remain internal")
                target = spec
                for part in reference[2:].split("/"):
                    part = part.replace("~1", "/").replace("~0", "~")
                    require(isinstance(target, dict) and part in target,
                            f"Commons OpenAPI reference is unresolved: {reference}")
                    target = target[part]
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    references(spec)


def validate_founding_mission(value: dict) -> None:
    fields(value, {"schema_version", "id", "title", "status", "founding_statement", "summary", "homepage", "source",
                   "updated", "participants", "value_principle", "outcomes", "first_contributions", "participation_url", "api", "trust_boundary"}, "founding mission")
    require(value["schema_version"] == "1.0" and value["id"] == "build-the-commons" and value["status"] == "open",
            "founding mission contract differs")
    calendar_date(value["updated"], "founding mission.updated")
    for name in ("title", "summary", "value_principle", "trust_boundary"):
        text_field(value[name], f"founding mission.{name}", 1000)
    fields(value["founding_statement"], {"de", "en"}, "founding statement")
    for language, statement in value["founding_statement"].items():
        text_field(statement, f"founding statement.{language}")
    for name in ("participants", "outcomes"):
        string_array(value[name], f"founding mission.{name}")
    require(value["homepage"] == ORIGIN + "/mission/" and value["participation_url"] == ORIGIN + "/singularity/" and value["api"] == ORIGIN + "/api/v1",
            "founding mission routes differ")
    https_url(value["source"], "founding mission.source")
    require(isinstance(value["first_contributions"], list) and len(value["first_contributions"]) == 4,
            "founding mission must describe four initial contribution types")
    kinds = set()
    for contribution in value["first_contributions"]:
        fields(contribution, {"kind", "task"}, "founding contribution")
        text_field(contribution["task"], "founding contribution.task")
        require(contribution["kind"] in {"mission", "field-note", "project", "review"}, "unsupported founding contribution kind")
        kinds.add(contribution["kind"])
    require(len(kinds) == 4, "founding contribution kinds must be distinct")


def validate_help_wanted(value: dict, schema: dict) -> None:
    require(schema.get("$id") == ORIGIN + "/data/help-wanted.schema.json", "incorrect help schema identifier")
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "incorrect help schema dialect")
    validate_manifest(value, schema, "help requests")
    expected_ids = ["trust-boundary-tests", "accessibility-check", "bug-reproduction", "atlas-freshness", "machine-contract-review", "small-patch"]
    require([request["id"] for request in value["requests"]] == expected_ids,
            "help request identifiers or security-first ordering differ")
    require("private_security" in value["requests"][0]["submit_via"], "security findings need a private reporting route")


def check(root: Path) -> tuple[int, int]:
    manifest = read_json(root / ".well-known/agent-home.json")
    schema = read_json(root / "data/agent-home.schema.json")
    require(schema.get("$id") == ORIGIN + "/data/agent-home.schema.json", "incorrect manifest schema identifier")
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "incorrect JSON Schema dialect")
    validate_manifest(manifest, schema)
    urls = [manifest["$schema"], manifest["homepage"], *manifest["pages"].values()]
    urls.extend(resource["url"] for resource in manifest["resources"].values())
    workshop = manifest["services"]["workshop"]
    urls.extend(workshop[key] for key in ("homepage", "collaboration_home", "discovery", "openapi"))
    for url in urls:
        if url not in RUNTIME_DISCOVERY_URLS:
            local_target(root, url)
    overview = (root / "llms.txt").read_text(encoding="utf-8")
    require(overview.startswith("# OSS Singularity\n") and overview.endswith("\n"), "llms.txt must have a title and final newline")
    for url in urls:
        if url not in {manifest["homepage"], ORIGIN + "/llms.txt"}:
            require(url in overview, f"llms.txt is missing discovery URL: {url}")
    require(ORIGIN + "/.well-known/agent-home.json" in overview, "llms.txt must link to the manifest")
    atlas = read_json(local_target(root, manifest["resources"]["atlas"]["url"]))
    missions = read_json(local_target(root, manifest["resources"]["missions"]["url"]))
    validate_openapi(read_json(local_target(root, workshop["openapi"])))
    validate_founding_mission(read_json(local_target(root, manifest["resources"]["founding_mission"]["url"])))
    help_data = read_json(local_target(root, manifest["resources"]["help_wanted"]["url"]))
    help_schema = read_json(local_target(root, help_data["$schema"]))
    validate_help_wanted(help_data, help_schema)
    local_target(root, help_data["homepage"])
    local_target(root, help_data["mission_url"])
    local_target(root, help_data["submission_routes"]["workshop"])
    local_target(root, help_data["submission_routes"]["security_contact"])
    return validate_atlas(atlas), validate_missions(missions)


def self_test() -> int:
    source = Path(__file__).resolve().parent.parent / "site"
    manifest = read_json(source / ".well-known/agent-home.json")
    schema = read_json(source / "data/agent-home.schema.json")
    openapi = read_json(source / "data/commons-openapi.json")
    founding = read_json(source / "data/founding-mission.json")
    help_data = read_json(source / "data/help-wanted.json")
    help_schema = read_json(source / "data/help-wanted.schema.json")
    today = datetime.now(timezone.utc).date().isoformat()
    atlas = {"schema_version": "1.1", "updated": today, "entries": [{
        "id": "example-agent", "name": "Example", "category": "coding", "summary": "An example tool.",
        "use_case": "Inspect a sample.", "website": "https://example.org/", "source_url": "https://example.org/source",
        "license": "MIT", "reviewed": today, "tags": ["example"],
    }]}
    missions = {"schema_version": "1.0", "updated": today, "missions": [{
        "id": "example-mission", "title": "Example", "summary": "An example mission.", "goal": "Inspect a sample.",
        "deliverable": "A report.", "constraints": ["Use public data."], "acceptance": ["Cite the source."],
    }]}
    validate_manifest(manifest, schema)
    validate_atlas(atlas)
    personal = copy.deepcopy(atlas)
    personal["entries"][0]["category"] = "personal"
    validate_atlas(personal)
    validate_missions(missions)
    validate_openapi(openapi)
    validate_founding_mission(founding)
    validate_help_wanted(help_data, help_schema)
    cases = 0

    def rejected(operation) -> None:
        nonlocal cases
        try:
            operation()
        except ContractError:
            cases += 1
        else:
            raise ContractError("self-test accepted an invalid contract")

    for key, value in (("schema_version", "2.0"), ("kind", "live-agent")):
        invalid = copy.deepcopy(manifest)
        invalid[key] = value
        rejected(lambda: validate_manifest(invalid, schema))
    for key, value in (("agent_execution", True), ("registration_api", True), ("a2a_endpoint", 0)):
        invalid = copy.deepcopy(manifest)
        invalid["interface"][key] = value
        rejected(lambda: validate_manifest(invalid, schema))
    invalid = copy.deepcopy(manifest)
    invalid["trust"]["execution_authority"] = "operator"
    rejected(lambda: validate_manifest(invalid, schema))
    invalid = copy.deepcopy(manifest)
    invalid["services"]["workshop"]["discovery"] = ORIGIN + "/api/v1/unimplemented"
    rejected(lambda: validate_manifest(invalid, schema))
    invalid = copy.deepcopy(manifest)
    invalid["services"]["workshop"]["agent_execution"] = True
    rejected(lambda: validate_manifest(invalid, schema))
    invalid = copy.deepcopy(openapi)
    invalid["paths"]["/api/v1/proposals/{id}"]["get"]["security"] = []
    rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(openapi)
    invalid["paths"]["/api/v1/admin/proposals"] = {"get": {}}
    rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(openapi)
    invalid["components"]["schemas"]["ProposalReceipt"]["properties"]["status"] = {"const": "published"}
    rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(openapi)
    invalid["paths"]["/api/v1"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = "https://example.org/schema"
    rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(openapi)
    invalid["paths"]["/api/v1/identities"]["post"]["security"] = []
    rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(openapi)
    invalid["components"]["schemas"]["ChallengeProof"]["properties"]["challenge_token"] = {"type": "string"}
    rejected(lambda: validate_openapi(invalid))
    for path, method in (("/api/v1/participations", "post"), ("/api/v1/participations/mine", "get"),
                         ("/api/v1/participations/{id}", "get"), ("/api/v1/participations/{id}", "patch")):
        invalid = copy.deepcopy(openapi)
        invalid["paths"][path][method]["security"] = []
        rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(openapi)
    invalid["components"]["schemas"]["ParticipationRequest"]["properties"]["identity_id"] = {"type": "string"}
    rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(openapi)
    invalid["components"]["schemas"]["ParticipationStateRequest"]["properties"]["state"]["enum"].append("active")
    rejected(lambda: validate_openapi(invalid))
    invalid = copy.deepcopy(founding)
    invalid["api"] = ORIGIN + "/api/unimplemented"
    rejected(lambda: validate_founding_mission(invalid))
    for key in ("production_authorized", "sibling_sites_authorized", "hosting_infrastructure_authorized", "third_party_services_authorized"):
        invalid = copy.deepcopy(help_data)
        invalid["security_scope"][key] = True
        rejected(lambda: validate_help_wanted(invalid, help_schema))
    invalid = copy.deepcopy(help_data)
    invalid["participation"]["operator_authorization_required"] = False
    rejected(lambda: validate_help_wanted(invalid, help_schema))
    invalid = copy.deepcopy(help_data)
    invalid["participation"]["paid_work_offer"] = True
    rejected(lambda: validate_help_wanted(invalid, help_schema))
    invalid = copy.deepcopy(help_data)
    invalid["requests"][1]["id"] = invalid["requests"][0]["id"]
    rejected(lambda: validate_help_wanted(invalid, help_schema))
    invalid = copy.deepcopy(help_data)
    invalid["requests"][0]["submit_via"] = ["workshop"]
    rejected(lambda: validate_help_wanted(invalid, help_schema))
    for version in ("1.0", "2.0"):
        invalid = copy.deepcopy(personal)
        invalid["schema_version"] = version
        rejected(lambda: validate_atlas(invalid))
    for key, value in (("source_url", "http://example.org/source"), ("source_url", "https://token:secret@example.org/"),
                       ("source_url", "https://127.0.0.1/"), ("source_url", "https://example.org\\@other.org/"),
                       ("category", "unknown"), ("reviewed", "2026-02-30"), ("reviewed", "9999-01-01"),
                       ("license", ""), ("id", "../outside"), ("tags", ["duplicate", "duplicate"])):
        invalid = copy.deepcopy(atlas)
        invalid["entries"][0][key] = value
        rejected(lambda: validate_atlas(invalid))
    invalid = copy.deepcopy(atlas)
    invalid["entries"].append(copy.deepcopy(invalid["entries"][0]))
    rejected(lambda: validate_atlas(invalid))
    invalid = copy.deepcopy(missions)
    invalid["missions"][0]["acceptance"] = []
    rejected(lambda: validate_missions(invalid))
    invalid = copy.deepcopy(missions)
    invalid["missions"][0]["constraints"] = "Run everything"
    rejected(lambda: validate_missions(invalid))
    invalid = copy.deepcopy(missions)
    invalid["missions"].append(copy.deepcopy(invalid["missions"][0]))
    rejected(lambda: validate_missions(invalid))
    with tempfile.TemporaryDirectory(prefix="oss-agent-data-") as temporary:
        tree = Path(temporary) / "site"
        tree.mkdir()
        (tree / "index.html").write_text("ok\n", encoding="utf-8")
        local_target(tree, ORIGIN + "/")
        outside = Path(temporary) / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        (tree / "escape.json").symlink_to(outside)
        for suffix in ("/../outside.json", "/%2e%2e/outside.json", "//outside.json", "/escape.json", "/missing.json", "/?mode=execute"):
            rejected(lambda: local_target(tree, ORIGIN + suffix))
        rejected(lambda: local_target(tree, "https://example.org/"))
        duplicate = tree / "duplicate.json"
        duplicate.write_text('{"id":"one","id":"two"}\n', encoding="utf-8")
        rejected(lambda: read_json(duplicate))
        duplicate.write_text('{"value":NaN}\n', encoding="utf-8")
        rejected(lambda: read_json(duplicate))
    print(f"agent data self-tests passed: {cases} invalid cases rejected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="dist", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            return self_test()
        entries, missions = check(args.root.resolve())
    except (ContractError, OSError, ValueError) as error:
        parser.exit(1, f"agent data check failed: {error}\n")
    print(f"agent data checks passed: {entries} Atlas entries, {missions} mission templates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
