#!/usr/bin/env python3
"""Fetch recent posts through twitter-api-safe-relay and write domain rules."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CATALOG_URL = os.getenv(
    "TWITTER_REQUEST_CATALOG_URL",
    "https://raw.githubusercontent.com/fa0311/twitter_api_safe_relay_skills/main/skills/twitter-api-relay/requests.ndjson",
)
USERNAME = os.getenv("TWITTER_USERNAME", "masaomi346").lstrip("@")
BASE_URL = os.getenv("TWITTER_RELAY_BASE_URL", "").rstrip("/")
TOKEN = os.getenv("TWITTER_RELAY_BEARER_TOKEN", "")
PROFILE = os.getenv("TWITTER_RELAY_PROFILE", "")
COUNT = max(1, min(int(os.getenv("TWEETS_PER_PAGE", "40")), 100))
MAX_PAGES = max(1, min(int(os.getenv("MAX_TWEET_PAGES", "10")), 100))
OUTPUT = Path(os.getenv("DOMAINS_OUTPUT", "ublockoriginbadwarefromx.txt"))

DOMAIN_RE = re.compile(
    r"(?<![a-z0-9-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9-])",
    re.IGNORECASE,
)
OMIT_HOSTS = ("virustotal.com", "urlscan.io")


def fail(message: str) -> "NoReturn":
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def http_get(url: str, *, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        fail(f"GET {url} returned HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        fail(f"GET {url} failed: {exc}")


def relay_get(path: str, params: dict[str, str]) -> Any:
    query = urllib.parse.urlencode(params)
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if PROFILE:
        headers["x-profile-name"] = PROFILE
    return http_get(f"{BASE_URL}/i/api{path}?{query}", headers=headers)


def catalog_operations() -> tuple[dict[str, Any], dict[str, Any]]:
    if not BASE_URL:
        fail("Set the TWITTER_RELAY_BASE_URL GitHub Actions secret to your reachable relay URL.")
    raw = urllib.request.urlopen(CATALOG_URL, timeout=45).read().decode("utf-8")
    operations: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = row.get("path", "")
        operation = path.rsplit("/", 1)[-1]
        if row.get("method") == "GET" and operation in {"UserByScreenName", "UserTweets"}:
            operations[operation] = row
    missing = {"UserByScreenName", "UserTweets"} - operations.keys()
    if missing:
        fail(f"The request catalog has no current GraphQL definition for: {', '.join(sorted(missing))}")
    return operations["UserByScreenName"], operations["UserTweets"]


def params_for(operation: dict[str, Any], variables: dict[str, Any]) -> dict[str, str]:
    params = dict(operation.get("params", {}))
    try:
        saved = json.loads(params.get("variables", "{}"))
    except json.JSONDecodeError:
        saved = {}
    saved.update(variables)
    params["variables"] = json.dumps(saved, separators=(",", ":"))
    return params


def rest_id(value: Any) -> str | None:
    if isinstance(value, dict):
        if value.get("rest_id"):
            return str(value["rest_id"])
        for child in value.values():
            found = rest_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = rest_id(child)
            if found:
                return found
    return None


def tweet_texts(value: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            legacy = node.get("legacy")
            if isinstance(legacy, dict):
                text = legacy.get("full_text") or legacy.get("text")
                tweet_id = str(legacy.get("id_str") or node.get("rest_id") or "")
                if isinstance(text, str) and (not tweet_id or tweet_id not in seen):
                    found.append(text)
                    if tweet_id:
                        seen.add(tweet_id)
            note = node.get("note_tweet_results")
            if isinstance(note, dict):
                result = note.get("result")
                if isinstance(result, dict) and isinstance(result.get("text"), str):
                    tweet_id = str(node.get("rest_id") or "")
                    if not tweet_id or tweet_id not in seen:
                        found.append(result["text"])
                        if tweet_id:
                            seen.add(tweet_id)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


def bottom_cursor(value: Any) -> str | None:
    cursors: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if str(node.get("cursorType", "")).lower() == "bottom" and node.get("value"):
                cursors.append(str(node["value"]))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return cursors[-1] if cursors else None


def domains_from(texts: list[str]) -> list[str]:
    domains: set[str] = set()
    for text in texts:
        # X posts commonly defang URLs as hxxps:// or escape dots and slashes.
        normalized = text.replace("\\", "")
        normalized = re.sub(r"(?i)hxxps?", "https", normalized)
        normalized = re.sub(r"(?i)\[\.\]|\(\.\)|\{\.\}", ".", normalized)
        for match in DOMAIN_RE.finditer(normalized):
            host = match.group(0).lower().rstrip(".")
            if any(host == excluded or host.endswith("." + excluded) for excluded in OMIT_HOSTS):
                continue
            domains.add(host)
    return sorted(domains)


def main() -> None:
    by_name, user_tweets = catalog_operations()
    profile_path = by_name["path"]
    profile_params = params_for(by_name, {"screen_name": USERNAME})
    profile = relay_get(profile_path, profile_params)
    user_id = rest_id(profile)
    if not user_id:
        fail(f"Could not find the X user ID for @{USERNAME}; check relay access and the UserByScreenName response.")

    timeline_path = user_tweets["path"]
    texts: list[str] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        variables: dict[str, Any] = {
            "userId": user_id,
            "count": COUNT,
            "includePromotedContent": False,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor
        result = relay_get(timeline_path, params_for(user_tweets, variables))
        page_texts = tweet_texts(result)
        texts.extend(page_texts)
        next_cursor = bottom_cursor(result)
        if not next_cursor or next_cursor == cursor or not page_texts:
            break
        cursor = next_cursor

    if not texts:
        fail("The relay returned no post text; leaving the existing output file unchanged.")

    domains = domains_from(texts)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("".join(f"||{domain}^\n" for domain in domains), encoding="utf-8")
    print(f"Read {len(texts)} post text(s) from @{USERNAME}; wrote {len(domains)} unique domain(s) to {OUTPUT}.")


if __name__ == "__main__":
    main()
