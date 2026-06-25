#!/usr/bin/env python3
"""
Update GitHub profile README with public repository statistics.

Reads from environment:
  - GITHUB_TOKEN (required): GitHub personal access token
  - PROFILE_USERNAME or GITHUB_REPOSITORY_OWNER (required): GitHub username
  - PROFILE_TIMEZONE (optional): timezone string, default Asia/Shanghai

Only modifies content between <!-- AUTO-GITHUB:START --> and <!-- AUTO-GITHUB:END --> markers.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Configuration ──────────────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 30
PER_PAGE = 100
MAX_DISPLAY_PROJECTS = 6


# ── Timezone helpers ───────────────────────────────────────────────────────


def get_timezone() -> timezone:
    """Parse PROFILE_TIMEZONE or default to Asia/Shanghai (UTC+8)."""
    tz_name = os.environ.get("PROFILE_TIMEZONE", "Asia/Shanghai")
    offset_map: dict[str, timedelta] = {
        "Asia/Shanghai": timedelta(hours=8),
        "Asia/Tokyo": timedelta(hours=9),
        "Asia/Singapore": timedelta(hours=8),
        "Asia/Hong_Kong": timedelta(hours=8),
        "America/New_York": timedelta(hours=-5),
        "America/Chicago": timedelta(hours=-6),
        "America/Denver": timedelta(hours=-7),
        "America/Los_Angeles": timedelta(hours=-8),
        "Europe/London": timedelta(hours=0),
        "Europe/Berlin": timedelta(hours=1),
        "Europe/Paris": timedelta(hours=1),
        "UTC": timedelta(hours=0),
    }
    offset = offset_map.get(tz_name)
    if offset is not None:
        return timezone(offset, tz_name)
    print(
        f"  [warn] Unknown timezone '{tz_name}', falling back to Asia/Shanghai (UTC+8)",
        file=sys.stderr,
    )
    return timezone(timedelta(hours=8), "Asia/Shanghai")


def format_datetime(dt: datetime, tz: timezone) -> str:
    """Format a datetime with the given timezone."""
    local_dt = dt.astimezone(tz)
    return local_dt.strftime("%Y-%m-%d %H:%M")


def now_in_timezone(tz: timezone) -> datetime:
    """Return current time in the given timezone."""
    return datetime.now(timezone.utc).astimezone(tz)


# ── HTTP helpers ───────────────────────────────────────────────────────────


def build_headers(token: str) -> dict[str, str]:
    """Build HTTP headers for GitHub API requests."""
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "profile-update-script",
    }


def api_get(url: str, token: str) -> tuple[Any, dict[str, str]]:
    """Make a GET request to the GitHub API. Returns (data, headers)."""
    req = urllib.request.Request(url, headers=build_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  [error] HTTP {e.code} for {url}", file=sys.stderr)
        print(f"  [error] Response: {body[:500]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"  [error] Network error for {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def paginate(url: str, token: str) -> list[Any]:
    """Fetch all pages of a paginated GitHub API endpoint."""
    results = []
    while url:
        data, headers = api_get(url, token)
        if isinstance(data, list):
            results.extend(data)
        else:
            results.append(data)
            break
        # Check for next page in Link header
        link_header = headers.get("Link", "")
        next_url: str | None = None
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                match = re.search(r"<([^>]+)>", part)
                if match:
                    next_url = match.group(1)
        url = next_url  # type: ignore[assignment]
    return results


# ── GitHub data fetching ──────────────────────────────────────────────────


def get_user(username: str, token: str) -> dict[str, Any]:
    """Fetch public profile for a GitHub user."""
    url = f"{GITHUB_API}/users/{username}"
    data, _ = api_get(url, token)
    return data  # type: ignore[return-value]


def get_all_public_repos(username: str, token: str) -> list[dict[str, Any]]:
    """Fetch all public repos for a user, handling pagination."""
    url = f"{GITHUB_API}/users/{username}/repos?per_page={PER_PAGE}&type=public"
    return paginate(url, token)


def filter_repos(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out fork, archived, and private repos."""
    filtered = []
    for repo in repos:
        if repo.get("fork", False):
            continue
        if repo.get("archived", False):
            continue
        if repo.get("private", False):
            continue
        filtered.append(repo)
    return filtered


def compute_stats(repos: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    """Compute total stars and return top repos sorted by stars."""
    total_stars = sum(repo.get("stargazers_count", 0) for repo in repos)
    sorted_repos = sorted(
        repos,
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")),
        reverse=True,
    )
    return total_stars, sorted_repos


# ── Markdown formatting ───────────────────────────────────────────────────


def escape_table(text: str | None) -> str:
    """Escape pipe characters and trim for markdown table cell safety."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("|", "\\|")
    text = text.replace("\n", " ").replace("\r", " ")
    return text.strip()


def format_repo_row(repo: dict[str, Any]) -> str:
    """Format a single repository as a markdown table row."""
    name = repo.get("name", "unknown")
    description = escape_table(repo.get("description")) or "\u2014"
    language = repo.get("language") or "\u2014"
    stars = repo.get("stargazers_count", 0)
    repo_url = repo.get("html_url", f"https://github.com/{repo.get('full_name', '')}")

    project_cell = f"[{escape_table(name)}]({repo_url})"
    return f"| {project_cell} | {description} | {escape_table(language)} | \u2b50 {stars} |"


def build_profile_section(
    public_repos_count: int,
    followers: int,
    total_stars: int,
    top_repos: list[dict[str, Any]],
    tz: timezone,
) -> str:
    """Build the content to insert between AUTO-GITHUB markers."""
    lines = []

    # Summary line
    lines.append(
        f"\U0001f52d \u516c\u5f00\u4ed3\u5e93 **{public_repos_count}** \u00b7 "
        f"\U0001f465 Followers **{followers}** \u00b7 "
        f"\u2b50 \u7d2f\u8ba1\u83b7\u5f97 Star **{total_stars}**"
    )
    lines.append("")

    # Projects table
    if top_repos:
        lines.append("### \U0001f4cc \u7cbe\u9009\u516c\u5f00\u9879\u76ee")
        lines.append("")
        lines.append("| \u9879\u76ee | \u7b80\u4ecb | \u6280\u672f | Star |")
        lines.append("|:---|---:|:---:|:---:|")
        for repo in top_repos:
            lines.append(format_repo_row(repo))
        lines.append("")

    # Last updated
    now = now_in_timezone(tz)
    tz_name = tz.tzname(None) if hasattr(tz, "tzname") else str(tz)
    lines.append(
        f"<sub>\U0001f550 \u6700\u540e\u66f4\u65b0\uff1a{format_datetime(now, tz)} \u00b7 {tz_name}</sub>"
    )

    return "\n".join(lines) + "\n"


# ── README update ─────────────────────────────────────────────────────────

MARKER_START = "<!-- AUTO-GITHUB:START -->"
MARKER_END = "<!-- AUTO-GITHUB:END -->"


def update_readme(readme_path: str, new_content: str) -> bool:
    """Replace content between markers in README.md. Returns True if changed."""
    if not os.path.isfile(readme_path):
        print(f"  [error] README not found at {readme_path}", file=sys.stderr)
        sys.exit(1)

    with open(readme_path, encoding="utf-8") as f:
        content = f.read()

    start_idx = content.find(MARKER_START)
    end_idx = content.find(MARKER_END)

    if start_idx == -1 or end_idx == -1:
        print(
            "  [error] README.md is missing AUTO-GITHUB markers.\n"
            f"  Please add the following to README.md:\n"
            f"  {MARKER_START}",
            file=sys.stderr,
        )
        sys.exit(1)

    end_idx = end_idx + len(MARKER_END)

    before = content[: start_idx + len(MARKER_START)]
    after = content[end_idx:]

    new_readme = before + "\n" + new_content + "\n" + MARKER_END + "\n" + after

    if new_readme == content:
        return False

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new_readme)
    return True


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    """Main entry point."""
    # Read environment
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "  [error] GITHUB_TOKEN environment variable is required.", file=sys.stderr
        )
        sys.exit(1)

    username = os.environ.get("PROFILE_USERNAME") or os.environ.get(
        "GITHUB_REPOSITORY_OWNER"
    )
    if not username:
        print(
            "  [error] PROFILE_USERNAME or GITHUB_REPOSITORY_OWNER environment variable is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    tz = get_timezone()

    print(f"  Fetching profile for {username} ...")

    # Fetch user profile
    user = get_user(username, token)
    public_repos_count = user.get("public_repos", 0)
    followers = user.get("followers", 0)
    print(f"  Public repos: {public_repos_count}, Followers: {followers}")

    # Fetch all public repos
    print("  Fetching public repositories ...")
    all_repos = get_all_public_repos(username, token)
    print(f"  Total public repos returned: {len(all_repos)}")

    # Filter
    filtered = filter_repos(all_repos)
    print(f"  After filtering (no fork, no archived): {len(filtered)}")

    # Compute stats
    total_stars, sorted_repos = compute_stats(filtered)
    print(f"  Total stars: {total_stars}")

    # Top projects
    top_repos = sorted_repos[:MAX_DISPLAY_PROJECTS]
    print(f"  Top {len(top_repos)} projects for display")

    # Build new content
    new_content = build_profile_section(
        public_repos_count, followers, total_stars, top_repos, tz
    )

    # Update README
    readme_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "README.md"
    )
    readme_path = os.path.normpath(readme_path)

    changed = update_readme(readme_path, new_content)

    if changed:
        print("  README.md updated successfully")
    else:
        print("  No changes to README.md (data is up-to-date)")

    print("  Done.")


if __name__ == "__main__":
    main()
