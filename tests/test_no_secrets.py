"""Spärr: inga nycklar eller lösenord i filer som git följer."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),          # Anthropic-nycklar
    # ett ifyllt värde på samma rad, t.ex. AUTH_PASSWORD=något
    re.compile(r"AUTH_PASSWORD[ \t]*[=:][ \t]*['\"]?[^\s'\"#]+"),
    re.compile(r"ANTHROPIC_API_KEY[ \t]*[=:][ \t]*['\"]?[^\s'\"#]+"),
]
ALLOWED = {"tests/test_no_secrets.py"}


def tracked_and_staged_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f and f not in ALLOWED and not f.startswith("data/")]


def test_env_file_is_ignored():
    assert subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT).returncode == 0


def test_no_secrets_in_files_git_would_commit():
    leaks = []
    for name in tracked_and_staged_files():
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                # never print the match itself: it could be a real secret
                leaks.append(f"{name}:{text.count(chr(10), 0, match.start()) + 1} ({pattern.pattern[:20]})")
    assert not leaks, "Possible secrets in files git would commit:\n" + "\n".join(leaks)
