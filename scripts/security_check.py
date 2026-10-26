#!/usr/bin/env python3
"""Repository security check for the Clinical AI Platform synthetic-data demo.

Runs locally and in CI (stdlib only):

    python3 scripts/security_check.py

Exit codes: 0 = clean (warnings allowed), 1 = findings or internal error.

Sections (each prints counts, then findings as `file:line` + PATTERN NAME):
  1. secret-scan      known key shapes + high-entropy secret assignments
  2. forbidden-files  files that must never be tracked (.env, keys, dumps, ...)
  3. gitignore        .env is covered by .gitignore (warning only if not)
  4. frontend-hygiene no external resources, web storage, or console logging
  5. db-credentials   no DB passwords / connection strings with passwords

The suspected secret VALUE is never printed: only file:line and pattern name.

Allowlist logic (how false positives stay at zero on this repo):
  * .env.example is exempt from every check except the strong key patterns
    (private keys and AWS keys are still flagged there if ever pasted in).
  * Test fixtures (tests/, conftest.py, test_*.py, *_test.py, fixtures/) are
    exempt from the heuristic checks (high-entropy assignments, db-credentials).
  * Values that look like placeholders are never flagged: change-me, replace-me,
    example, dummy, placeholder, sample, test-only, not-a-real, ci-only, your-*,
    redacted, xxxx, ${...} / <...> references. This is what keeps the documented
    .env.example values and test sentinels green.
  * Docstring/URL examples whose credentials are generic words (user:password@host)
    are ignored.
  * A line carrying the marker `security-check-allow` is skipped; use it only
    for a reviewed false positive and write the reason next to it.

Optional dependency-audit mode (used by the CI "security" job):

    python3 scripts/security_check.py --audit-json <pip-audit.json>

Reads `uvx pip-audit --format json` output, resolves each advisory's severity
through the OSV database, and FAILS on HIGH/CRITICAL (MODERATE/LOW warn).
Overrides (set as GitHub repository *variables*, never secrets):
  SECURITY_AUDIT_ALLOW  comma-separated advisory IDs to accept while a fix is
                        prepared (each needs a tracked remediation issue)
  SECURITY_AUDIT_MODE   "warn" downgrades the gate to a warning
Advisories whose severity cannot be resolved are treated as blocking.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- patterns (names are what gets printed; values never are) ---------------

PATTERNS_STRONG = [
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("opencode-api-key", re.compile(r"\boc_sk_[A-Za-z0-9_\-]{8,}")),
]

ASSIGNMENT = re.compile(
    r"\b([A-Za-z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD)[A-Za-z0-9_]*)\s*[:=]\s*"
    r"[\"']?([A-Za-z0-9+/=_.@\-]{20,})[\"']?",
    re.IGNORECASE,
)

DB_URL_WITH_PASSWORD = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|maria(?:db)?|mongodb|rediss?|amqps?)(?:\+[a-z0-9]+)?"
    r"://(?P<user>[^\s:/@\"'`]+):(?P<password>[^\s@/\"'`]+)@"
)
DB_PASSWORD_ASSIGN = re.compile(
    r"\b(?:POSTGRES_PASSWORD|PGPASSWORD|DATABASE_PASSWORD|DB_PASSWORD|MIGRATION_PASSWORD)"
    r"\s*[:=]\s*[\"']?([^\"'\s#]+)"
)

# Generic words used in docstring/URL examples (user:password@host and friends).
GENERIC_CREDENTIAL = re.compile(
    r"(?i)^(password|passwd|pass|pwd|secret|token|key|user|username|admin|"
    r"changeme|change[-_]?me|replace[-_]?me|example|\*+|x+|\.+)$"
)

PLACEHOLDER = re.compile(
    r"(?i)(change[-_ ]?me|replace[-_ ]?me|example|dummy|placeholder|sample|"
    r"test[-_ ]?only|not[-_ ]?a[-_ ]?real|ci[-_ ]?only|your[-_ ]|redacted|fake|mock|"
    r"xxx+|todo|fixme|<[a-z _]+>|\$\{|\%\()"
)

TEST_PATH = re.compile(r"(^|/)(tests?|fixtures?|conftest\.py|test_[^/]+\.py|[^/]*_test\.py)(/|$)")

SUPPRESS_MARKER = "security-check-allow"

HTML_EXTERNAL_REF = re.compile(r"(?:src|href)\s*=\s*[\"'](https?://[^\"']+)[\"']", re.IGNORECASE)
HTML_CSS_IMPORT = re.compile(r"@import\s+[^;]*url\(\s*[\"']?https?://", re.IGNORECASE)
JS_CONSOLE = re.compile(r"console\s*\.")
JS_WEB_STORAGE = re.compile(r"(local|session)Storage")

FORBIDDEN_PATHS = [
    ("env-file", re.compile(r"(^|/)\.env(\.[^/]+)?$"), {".env.example"}),
    ("private-key-file", re.compile(r"\.pem$"), set()),
    ("key-file", re.compile(r"\.key$"), set()),
    ("db-dump", re.compile(r"(\.dump|\.sql\.gz|\.sqlite3?)$"), set()),
    ("virtualenv", re.compile(r"(^|/)\.?venv/"), set()),
    ("node-modules", re.compile(r"(^|/)node_modules/"), set()),
    ("db-volume-dir", re.compile(r"(^|/)(db-data|postgres-data)/"), set()),
]

GITIGNORE_REQUIRED = ".env"


def line_no(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.search(value))


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def shannon_entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def looks_high_entropy(value: str) -> bool:
    return (
        len(value) >= 20
        and shannon_entropy(value) >= 3.5
        and any(c.isdigit() for c in value)
        and any(c.isalpha() for c in value)
        and any(c.isupper() for c in value)
        and any(c.islower() for c in value)
    )


def strip_comments_keep_lines(source: str) -> str:
    """Blank out comments but keep every newline so line numbers stay true."""

    def blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    out = re.sub(r"/\*.*?\*/", blank, source, flags=re.S)
    return re.sub(r"^\s*//.*$", blank, out, flags=re.M)


def tracked_files() -> list[str]:
    """READ-ONLY git listing of tracked files. No other git command is used."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        print("internal error: git is not available; cannot list tracked files")
        sys.exit(1)
    if result.returncode != 0:
        print("internal error: `git ls-files` failed; is this a git repository?")
        sys.exit(1)
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def read_text(path: str) -> str | None:
    raw = (REPO_ROOT / path).read_bytes()
    if b"\0" in raw[:8192]:
        return None  # binary
    return raw.decode("utf-8", "replace")


# --- checks -----------------------------------------------------------------


def check_secret_scan(files: list[str]) -> tuple[list[str], int]:
    findings: list[str] = []
    scanned = 0
    for path in files:
        text = read_text(path)
        if text is None:
            continue
        scanned += 1
        exempt_heuristic = path == ".env.example" or is_test_path(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if SUPPRESS_MARKER in line.lower():
                continue
            for name, pattern in PATTERNS_STRONG:
                if name == "opencode-api-key" and path == ".env.example":
                    continue
                for match in pattern.finditer(line):
                    if is_placeholder(match.group(0)):
                        continue
                    findings.append(
                        f"{path}:{lineno} [{name}] committed key material; "
                        f"rotate the credential and remove it from history"
                    )
            if exempt_heuristic:
                continue
            for match in ASSIGNMENT.finditer(line):
                value = match.group(2)
                if is_placeholder(value) or not looks_high_entropy(value):
                    continue
                findings.append(
                    f"{path}:{lineno} [high-entropy-secret] value assigned to "
                    f"{match.group(1)}; move it to an environment variable and rotate it"
                )
    return findings, scanned


def check_forbidden_files(files: list[str]) -> tuple[list[str], int]:
    findings: list[str] = []
    for path in files:
        for name, pattern, allowed in FORBIDDEN_PATHS:
            if path in allowed:
                continue
            if pattern.search(path):
                findings.append(
                    f"{path} [{name}] must not be tracked; remove it and keep it in .gitignore"
                )
    return findings, len(files)


def check_gitignore(files: list[str]) -> tuple[list[str], list[str], int]:
    findings: list[str] = []
    warnings: list[str] = []
    gitignore = REPO_ROOT / ".gitignore"
    if ".gitignore" not in files or not gitignore.is_file():
        warnings.append(".gitignore [missing] add one covering .env, keys, dumps, venvs")
        return findings, warnings, 0
    covered = False
    for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if line == GITIGNORE_REQUIRED or line == GITIGNORE_REQUIRED + "/" or line == "*.env":
            covered = True
    if not covered:
        warnings.append(
            f".gitignore [env-not-ignored] add a `{GITIGNORE_REQUIRED}` line so populated "
            f"env files can never be committed"
        )
    return findings, warnings, 1


def check_frontend_hygiene() -> tuple[list[str], int]:
    findings: list[str] = []
    checked = 0
    frontend = REPO_ROOT / "frontend"
    for html in sorted(frontend.rglob("*.html")):
        checked += 1
        rel = html.relative_to(REPO_ROOT).as_posix()
        text = html.read_text(encoding="utf-8", errors="replace")
        for match in HTML_EXTERNAL_REF.finditer(text):
            findings.append(
                f"{rel}:{line_no(text, match.start())} [external-resource] "
                f"load it locally; no third-party scripts/styles/fonts in the demo"
            )
        for match in HTML_CSS_IMPORT.finditer(text):
            findings.append(
                f"{rel}:{line_no(text, match.start())} [external-css-import] "
                f"inline the stylesheet instead"
            )
    js_dir = frontend / "assets" / "js"
    for js in sorted(js_dir.rglob("*.js")) if js_dir.is_dir() else []:
        checked += 1
        rel = js.relative_to(REPO_ROOT).as_posix()
        source = strip_comments_keep_lines(js.read_text(encoding="utf-8", errors="replace"))
        for pattern, name, hint in (
            (JS_CONSOLE, "console-logging", "remove console.*; never log app data"),
            (JS_WEB_STORAGE, "web-storage", "keep data out of local/session storage"),
        ):
            for match in pattern.finditer(source):
                findings.append(
                    f"{rel}:{line_no(source, match.start())} [{name}] {hint}"
                )
    return findings, checked


INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def check_edge_headers() -> tuple[list[str], int]:
    """Keep frontend/_headers (Cloudflare Pages) in sync with the backend CSP.

    The edge must serve the same protection as SecurityHeadersMiddleware: no
    'unsafe-inline'/'unsafe-eval', and `script-src` hashes must match the actual
    inline bootstrap blocks in the served HTML, or the site breaks under CSP
    (or silently loses its script protection).
    """
    import base64
    import hashlib

    findings: list[str] = []
    checked = 0
    headers_file = REPO_ROOT / "frontend" / "_headers"
    frontend = REPO_ROOT / "frontend"
    if not headers_file.is_file():
        findings.append(
            "frontend/_headers:1 [edge-headers-missing] "
            "edge responses need the security headers; see docs/DEPLOYMENT.md section 4"
        )
        return findings, checked

    text = headers_file.read_text(encoding="utf-8", errors="replace")
    rel = "frontend/_headers"
    checked += 1

    csp_match = re.search(r"^\s*Content-Security-Policy:\s*(.+)$", text, re.MULTILINE)
    if not csp_match:
        findings.append(f"{rel}:1 [edge-csp-missing] add a Content-Security-Policy header")
    else:
        csp = csp_match.group(1)
        for banned in ("unsafe-inline", "unsafe-eval"):
            if banned in csp:
                findings.append(
                    f"{rel}:{line_no(text, csp_match.start())} [edge-csp-{banned}] "
                    f"remove '{banned}'; use hashes for the inline bootstrap"
                )
        for required in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
            if not re.search(rf"^\s*{required}:\s*\S", text, re.MULTILINE):
                findings.append(f"{rel}:1 [edge-header-missing] add the {required} header")

    # The script-src hashes must equal the hashes of the real inline scripts.
    expected: set[str] = set()
    for html in sorted(frontend.rglob("*.html")):
        checked += 1
        for match in INLINE_SCRIPT_RE.finditer(html.read_text(encoding="utf-8", errors="replace")):
            digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
            expected.add(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    declared = set(re.findall(r"'sha256-[^']+'", csp_match.group(1) if csp_match else ""))
    if declared != expected:
        findings.append(
            f"{rel}:1 [edge-csp-hash-drift] script-src hashes do not match the inline "
            f"bootstrap blocks (declared {sorted(declared)}, actual {sorted(expected)})"
        )
    return findings, checked


def check_db_credentials(files: list[str]) -> tuple[list[str], int]:
    findings: list[str] = []
    checked = 0
    for path in files:
        text = read_text(path)
        if text is None:
            continue
        checked += 1
        exempt = path == ".env.example" or is_test_path(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if SUPPRESS_MARKER in line.lower() or exempt:
                continue
            for match in DB_URL_WITH_PASSWORD.finditer(line):
                if GENERIC_CREDENTIAL.match(match.group("password")):
                    continue
                findings.append(
                    f"{path}:{lineno} [db-connection-string-with-password] use env vars; "
                    f"never a literal connection string with credentials"
                )
            for match in DB_PASSWORD_ASSIGN.finditer(line):
                if is_placeholder(match.group(1)) or GENERIC_CREDENTIAL.match(match.group(1)):
                    continue
                findings.append(
                    f"{path}:{lineno} [db-superuser-password] literal database password; "
                    f"load it from the environment instead"
                )
    return findings, checked


# --- dependency audit mode --------------------------------------------------


def osv_severity(ids: list[str]) -> str:
    """Severity for an advisory from the OSV database (GHSA/CVE records carry it)."""
    for candidate in ids:
        try:
            with urllib.request.urlopen(
                f"https://api.osv.dev/v1/vulns/{candidate}", timeout=15
            ) as response:
                record = json.load(response)
        except Exception:  # noqa: BLE001 - lookup failure must not crash the gate
            continue
        severity = record.get("database_specific", {}).get("severity")
        if severity:
            return str(severity).upper()
    return "UNKNOWN"


def audit_mode(audit_json: Path) -> int:
    try:
        data = json.loads(audit_json.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"internal error: cannot read pip-audit output: {type(exc).__name__}")
        return 1

    advisories: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for dep in data.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            key = (dep.get("name", "?"), vuln.get("id", "?"))
            if key in seen:
                continue
            seen.add(key)
            advisories.append(
                {
                    "package": dep.get("name", "?"),
                    "version": dep.get("version", "?"),
                    "id": vuln.get("id", "?"),
                    "aliases": vuln.get("aliases", []),
                    "fix": vuln.get("fix_versions", []),
                }
            )

    print("== Dependency audit (pip-audit + OSV severity) ==")
    if not advisories:
        print("advisories: 0")
        print("RESULT: PASS")
        return 0

    allow = {
        item.strip().upper()
        for item in os.environ.get("SECURITY_AUDIT_ALLOW", "").split(",")
        if item.strip()
    }
    warn_only = os.environ.get("SECURITY_AUDIT_MODE", "fail") == "warn"

    blocking: list[str] = []
    warned: list[str] = []
    counts: Counter[str] = Counter()
    for advisory in advisories:
        severity = osv_severity([advisory["id"], *advisory["aliases"]])
        counts[severity] += 1
        accepted = bool({advisory["id"].upper(), *advisory["aliases"]} & allow)
        fix = ", ".join(advisory["fix"]) or "no fixed version published"
        line = (
            f"{advisory['package']} {advisory['version']} {advisory['id']} "
            f"[{severity}] fix: {fix}"
        )
        if accepted:
            warned.append(f"{line} (accepted via SECURITY_AUDIT_ALLOW)")
        elif severity in {"HIGH", "CRITICAL", "UNKNOWN"}:
            blocking.append(line)
        else:
            warned.append(line)

    summary = ", ".join(f"{name} {counts[name]}" for name in sorted(counts))
    print(f"advisories: {len(advisories)} ({summary})")
    for line in warned:
        print(f"WARN  {line}")
    for line in blocking:
        print(f"FAIL  {line}")

    if blocking and not warn_only:
        print(f"blocking: {len(blocking)}")
        fixes = sorted({f for a in advisories for f in a["fix"]})
        if fixes:
            print(f"fix: upgrade the affected packages (published fixed versions: {', '.join(fixes)})")
        print(
            "override: set repository variable SECURITY_AUDIT_ALLOW to the comma-separated "
            "advisory IDs (each with a tracked remediation issue), or SECURITY_AUDIT_MODE=warn "
            "to downgrade this gate temporarily"
        )
        print("RESULT: FAIL")
        return 1
    if blocking:
        print(f"blocking: {len(blocking)} (downgraded to warnings by SECURITY_AUDIT_MODE=warn)")
    print("RESULT: PASS" if not blocking else "RESULT: PASS (warn mode)")
    return 0


# --- main -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-json",
        type=Path,
        default=None,
        help="classify a pip-audit JSON report by severity instead of scanning the repo",
    )
    args = parser.parse_args()
    if args.audit_json is not None:
        return audit_mode(args.audit_json)

    files = tracked_files()
    print("Clinical AI Platform - repository security check")
    print(f"scope: {len(files)} tracked files (git ls-files)\n")

    sections = []
    findings, scanned = check_secret_scan(files)
    sections.append(("1/6 secret-scan", f"files scanned: {scanned}", findings, []))
    findings, counted = check_forbidden_files(files)
    sections.append(("2/6 forbidden-files", f"paths checked: {counted}", findings, []))
    findings, warnings, counted = check_gitignore(files)
    sections.append(("3/6 gitignore", f"files parsed: {counted}", findings, warnings))
    findings, counted = check_frontend_hygiene()
    sections.append(("4/6 frontend-hygiene", f"files checked: {counted}", findings, []))
    findings, counted = check_db_credentials(files)
    sections.append(("5/6 db-credentials", f"files scanned: {counted}", findings, []))
    findings, counted = check_edge_headers()
    sections.append(("6/6 edge-headers", f"files checked: {counted}", findings, []))

    total_findings = 0
    total_warnings = 0
    for title, count_line, section_findings, section_warnings in sections:
        print(f"== {title} ==")
        print(count_line)
        print(f"findings: {len(section_findings)}, warnings: {len(section_warnings)}")
        for line in section_warnings:
            print(f"WARN  {line}")
        for line in section_findings:
            print(f"FINDING  {line}")
        print()
        total_findings += len(section_findings)
        total_warnings += len(section_warnings)

    print(f"summary: 6 sections, {total_findings} findings, {total_warnings} warnings")
    print(f"RESULT: {'FAIL' if total_findings else 'PASS'}")
    return 1 if total_findings else 0


if __name__ == "__main__":
    sys.exit(main())
