"""
git_manager.py — Automated Git Tracking & Commit Management for the Egyptian Legal Pipeline.

Automates:
  1. Verification of security exclusion rules (.gitignore).
  2. Automatic staging of version-controlled code and configuration artifacts.
  3. Structured automated commits after pipeline runs.
  4. Generating push instructions and status summaries.
"""

import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("git_manager")
BASE_DIR = Path(__file__).resolve().parent


class GitPipelineManager:
    """
    Manages Git version control operations directly within pipeline workflows.
    """

    def __init__(self, repo_dir: Path = BASE_DIR):
        self.repo_dir = repo_dir

    def _run_git(self, args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args,
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def is_git_repo(self) -> bool:
        res = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return res.returncode == 0

    def init_repo(self) -> bool:
        if not self.is_git_repo():
            res = self._run_git(["init", "-b", "main"])
            if res.returncode == 0:
                logger.info("Initialized Git repository on branch 'main'.")
                return True
            else:
                logger.error("Git init failed: %s", res.stderr)
                return False
        return True

    def check_untracked_sensitive_files(self) -> List[str]:
        """
        Ensures credentials and raw datasets are NEVER tracked.
        Allows template files like .env.example and .env.local.example.
        """
        res = self._run_git(["status", "--porcelain"])
        violations = []
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                file_status = line.strip()
                # Check for credentials
                if "credentials.json" in file_status or "gcp_key.json" in file_status:
                    violations.append(file_status)
                # Check for raw/processed data dumps
                elif "raw_data" in file_status or "processed_data" in file_status:
                    violations.append(file_status)
                # Check for active .env files (excluding .example templates)
                elif ".env" in file_status and not file_status.endswith(".example"):
                    violations.append(file_status)
        return violations

    def stage_pipeline_files(self) -> bool:
        """
        Stages all allowed code, docs, and configs while respecting .gitignore.
        """
        res = self._run_git(["add", "."])
        return res.returncode == 0

    def commit_changes(self, commit_message: str) -> Optional[str]:
        """
        Commits staged files with a structured commit message.
        """
        # Check if there are changes to commit
        status = self._run_git(["status", "--porcelain"])
        if not status.stdout.strip():
            logger.info("Git working directory clean. Nothing to commit.")
            return None

        # Verify no sensitive files leaked into staging
        violations = self.check_untracked_sensitive_files()
        if violations:
            logger.error("Refusing to commit! Sensitive or data files detected: %s", violations)
            return None

        res = self._run_git(["commit", "-m", commit_message])
        if res.returncode == 0:
            logger.info("Git commit created: %s", commit_message.splitlines()[0])
            rev = self._run_git(["rev-parse", "--short", "HEAD"])
            return rev.stdout.strip()
        else:
            logger.warning("Git commit output: %s", res.stdout or res.stderr)
            return None

    def get_summary(self) -> Dict[str, str]:
        branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        last_commit = self._run_git(["log", "-1", "--oneline"]).stdout.strip()
        return {
            "branch": branch or "main",
            "last_commit": last_commit or "No commits yet",
        }


if __name__ == "__main__":
    gm = GitPipelineManager()
    gm.init_repo()
    print("Git Status:", gm.get_summary())
