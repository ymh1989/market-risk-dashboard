import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_local_market_update.sh"


def function(name):
    text = SCRIPT.read_text()
    return name + "() {" + text.split(name + "() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"


def run_git(tmp_path, error, failures, command='git_remote fetch origin main'):
    mock = r'''
git() {
  printf '%s|%s|%s\n' "$LC_ALL" "$GIT_TERMINAL_PROMPT" "$*" >> "$CALLS"
  count=$(wc -l < "$CALLS")
  if (( count <= FAILURES )); then
    printf '%s\n' "$ERROR" >&2
    return 128
  fi
  echo 'success'
}
sleep() { printf '%s\n' "$1" >> "$SLEEPS"; }
'''
    result = subprocess.run(
        ["/bin/bash", "-c", "set -Eu\n" + mock + function("git_remote")
         + function("push_update_commit") + command
         + '\nstatus=$?\nprintf "kind=%s\\n" "$GIT_REMOTE_FAILURE_KIND"\nexit "$status"'],
        env={**os.environ, "CALLS": str(tmp_path / "calls"), "SLEEPS": str(tmp_path / "sleeps"),
             "FAILURES": str(failures), "ERROR": error, "REMOTE": "origin", "BRANCH": "main"},
        text=True, capture_output=True, timeout=5,
    )
    calls = (tmp_path / "calls").read_text().splitlines()
    sleeps = (tmp_path / "sleeps").read_text().splitlines() if (tmp_path / "sleeps").exists() else []
    return result, calls, sleeps


def test_transient_ssh_failure_recovers_with_bounded_backoff(tmp_path):
    result, calls, sleeps = run_git(tmp_path, "Permission denied (publickey).", 2)
    assert result.returncode == 0
    assert calls == ["C|0|fetch origin main"] * 3
    assert sleeps == ["3", "6"]
    assert "kind=\n" in result.stdout


def test_exhausted_auth_failure_never_rebases_or_reports_success(tmp_path):
    result, calls, sleeps = run_git(tmp_path, "Permission denied (publickey).", 99, "push_update_commit")
    assert result.returncode == 128
    assert calls == ["C|0|push origin HEAD:main"] * 3
    assert sleeps == ["3", "6"]
    assert "재시도 소진" in result.stderr
    assert "kind=transport" in result.stdout
    assert "원격 변경을 감지" not in result.stdout


@pytest.mark.parametrize("error,kind", [
    ("Host key verification failed.\nfatal: Could not read from remote repository.", "host-key"),
    ("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", "host-key"),
    ("! [rejected] HEAD -> main (fetch first)", "remote-changed"),
    ("! [rejected] HEAD -> main (non-fast-forward)", "remote-changed"),
    ("remote: Repository not found.", "other"),
])
def test_non_transport_failures_do_not_retry(tmp_path, error, kind):
    result, calls, sleeps = run_git(tmp_path, error, 99)
    assert result.returncode == 128
    assert len(calls) == 1
    assert sleeps == []
    assert f"kind={kind}" in result.stdout


@pytest.mark.parametrize("error", ["Connection reset by peer", "Could not resolve hostname github.com", "HTTP 503"])
def test_connection_failures_retry_without_changing_git_arguments(tmp_path, error):
    result, calls, sleeps = run_git(tmp_path, error, 1, 'git_remote -C "/tmp/repo with spaces" fetch origin main')
    assert result.returncode == 0
    assert calls == ["C|0|-C /tmp/repo with spaces fetch origin main"] * 2
    assert sleeps == ["3"]
