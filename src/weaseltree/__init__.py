import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def is_native_windows() -> bool:
    """Check if running on native Windows (not WSL)."""
    return sys.platform == "win32"


def get_windows_home() -> Path:
    """Get the Windows user home directory."""
    if is_native_windows():
        return Path.home()
    # WSL: get Windows home via cmd.exe
    result = subprocess.run(
        ["cmd.exe", "/c", "echo", "%USERPROFILE%"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        win_path = result.stdout.strip()
        # Convert Windows path to WSL path
        wsl_result = subprocess.run(
            ["wslpath", "-u", win_path],
            capture_output=True,
            text=True,
        )
        if wsl_result.returncode == 0:
            return Path(wsl_result.stdout.strip())
    # Fallback to WSL home
    return Path.home()


def get_weaseltree_config() -> Path:
    """Get the path to the .weaseltree.json config file on Windows side."""
    return get_windows_home() / ".weaseltree.json"


def extract_relative_path(path: str) -> str | None:
    """Extract relative path from drive root.

    WSL: /mnt/c/r/foo/bar -> r/foo/bar
    Windows: C:\\r\\foo\\bar -> r/foo/bar
    """
    # WSL path
    match = re.match(r"^/mnt/[a-zA-Z]/(.*)$", path)
    if match:
        return match.group(1)
    # Native Windows path
    match = re.match(r"^[a-zA-Z]:[/\\](.*)$", path)
    if match:
        return match.group(1).replace("\\", "/")
    return None


def get_git_toplevel(cwd=None) -> str | None:
    """Get the root directory of the current git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def get_current_branch(cwd=None) -> str | None:
    """Get the current git branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode == 0:
        branch = result.stdout.strip()
        if branch != "HEAD":  # Not in detached HEAD state
            return branch
    return None


def detach_head(cwd=None):
    """Detach HEAD by writing commit SHA directly to .git/HEAD.

    This is much faster than `git checkout --detach` because it
    bypasses the working tree machinery.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to get current commit")
    commit_sha = result.stdout.strip()
    git_dir = cwd or "."
    with open(os.path.join(git_dir, ".git", "HEAD"), "w") as f:
        f.write(commit_sha + "\n")


def load_config() -> dict:
    """Load the full config from JSON file."""
    config_path = get_weaseltree_config()
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    """Save the full config to JSON file."""
    config_path = get_weaseltree_config()
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def save_repo_config(relative_path: str, branch: str, windows_path: str, wsl_path: str):
    """Save repo config to Windows ~/.weaseltree.json config file."""
    config = load_config()
    config[relative_path] = {
        "branch": branch,
        "windows_path": windows_path,
        "wsl_path": wsl_path,
    }
    save_config(config)


def load_repo_config(relative_path: str) -> dict | None:
    """Load repo config from Windows ~/.weaseltree.json config file."""
    config = load_config()
    return config.get(relative_path)


def find_config_by_wsl_path(wsl_path: str) -> tuple[str, dict] | None:
    """Find repo config by WSL path. Returns (relative_path, config) or None."""
    config = load_config()
    for key, entry in config.items():
        if isinstance(entry, dict) and entry.get("wsl_path") == wsl_path:
            return (key, entry)
    return None


def find_config_by_windows_path(windows_path: str) -> tuple[str, dict] | None:
    """Find repo config by Windows path. Returns (relative_path, config) or None."""
    config = load_config()
    for key, entry in config.items():
        if isinstance(entry, dict) and entry.get("windows_path") == windows_path:
            return (key, entry)
    return None


def ensure_win_remote(wsl_path: str, windows_path: str):
    """Ensure the WSL repo has a 'win' remote pointing to the Windows repo."""
    result = subprocess.run(
        ["git", "remote", "get-url", "win"],
        cwd=wsl_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        current_url = result.stdout.strip()
        if current_url != windows_path:
            subprocess.run(
                ["git", "remote", "set-url", "win", windows_path],
                cwd=wsl_path,
                check=True,
            )
    else:
        subprocess.run(
            ["git", "remote", "add", "win", windows_path],
            cwd=wsl_path,
            check=True,
        )


def resolve_config() -> tuple[str, dict]:
    """Resolve config from current directory. Returns (relative_path, config) or exits."""
    cwd = get_git_toplevel() or os.getcwd()

    # Try Windows side first (/mnt/c/...)
    relative_path = extract_relative_path(cwd)
    if relative_path is not None:
        config = load_repo_config(relative_path)
        if config:
            return relative_path, config

    # Try WSL side (lookup by wsl_path)
    result = find_config_by_wsl_path(cwd)
    if result is not None:
        return result

    print(f"Error: Not a weaseltree-managed path: {cwd}", file=sys.stderr)
    print(f"Run 'weaseltree link' first.", file=sys.stderr)
    sys.exit(1)


def link_command(args):
    """Link a WSL repo to its Windows counterpart."""
    cwd = get_git_toplevel() or os.getcwd()

    # Must be on WSL side (not under /mnt/)
    if extract_relative_path(cwd) is not None:
        print("Error: Run 'weaseltree link' from the WSL side, not /mnt/", file=sys.stderr)
        sys.exit(1)

    # Verify current dir is a git repo
    if not (Path(cwd) / ".git").is_dir():
        print(f"Error: Not a git repository: {cwd}", file=sys.stderr)
        sys.exit(1)

    windows_path = args.windows_path
    # Resolve the Windows path
    windows_path = str(Path(windows_path).resolve())

    # Verify Windows path is under /mnt/
    relative_path = extract_relative_path(windows_path)
    if relative_path is None:
        print(f"Error: Windows path must be under /mnt/<drive>/: {windows_path}", file=sys.stderr)
        sys.exit(1)

    # Verify Windows path is a git repo
    if not (Path(windows_path) / ".git").is_dir():
        print(f"Error: Not a git repository: {windows_path}", file=sys.stderr)
        sys.exit(1)

    current_branch = get_current_branch()
    if current_branch is None:
        print("Error: Not on a branch (detached HEAD?)", file=sys.stderr)
        sys.exit(1)

    # Add 'win' remote pointing to the Windows repo
    ensure_win_remote(cwd, windows_path)
    print(f"Remote 'win' -> {windows_path}")

    # Detach HEAD on Windows side (so push to branch ref works)
    try:
        detach_head(windows_path)
        print("Detached HEAD on Windows side")
    except Exception as e:
        print(f"Error detaching HEAD: {e}", file=sys.stderr)
        sys.exit(1)

    # Save config
    save_repo_config(relative_path, current_branch, windows_path, cwd)
    print(f"Saved config to {get_weaseltree_config()}")


def sync_command(args):
    """Push WSL branch to Windows repo and update Windows working tree."""
    relative_path, config = resolve_config()

    wsl_path = config["wsl_path"]
    windows_path = config["windows_path"]

    # Detect branch changes on WSL side
    current_branch = get_current_branch(wsl_path)
    if current_branch and current_branch != config["branch"]:
        print(f"Branch changed: {config['branch']} -> {current_branch}")
        config["branch"] = current_branch
        save_repo_config(relative_path, current_branch, windows_path, wsl_path)

    branch = config["branch"]

    # Ensure win remote is configured
    ensure_win_remote(wsl_path, windows_path)

    # Fetch from Windows to check if it has commits we don't have
    if not getattr(args, 'drop', False):
        fetch_result = subprocess.run(
            ["git", "fetch", "win", branch],
            cwd=wsl_path,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode == 0:
            # Check if Windows branch is ahead
            ahead_result = subprocess.run(
                ["git", "log", "--oneline", f"HEAD..win/{branch}"],
                cwd=wsl_path,
                capture_output=True,
                text=True,
            )
            if ahead_result.returncode == 0 and ahead_result.stdout.strip():
                lines = ahead_result.stdout.strip().splitlines()
                print(f"Windows side has {len(lines)} commit(s) not on '{branch}':")
                for line in lines:
                    print(f"  {line}")

                if getattr(args, 'pull', False):
                    response = "p"
                else:
                    try:
                        response = input("[p]ull to WSL branch / [d]rop / [a]bort? (use --pull or --drop to skip) ").strip().lower()
                    except EOFError:
                        response = ""

                if response == "p":
                    try:
                        subprocess.run(
                            ["git", "merge", f"win/{branch}"],
                            cwd=wsl_path,
                            check=True,
                        )
                        print(f"Merged Windows commits into '{branch}'")
                    except subprocess.CalledProcessError as e:
                        print(f"Error merging: {e}", file=sys.stderr)
                        print("Resolve conflicts manually, then run sync again.", file=sys.stderr)
                        sys.exit(1)
                elif response != "d":
                    print("Aborted.")
                    sys.exit(1)

    # Push WSL branch to Windows repo
    try:
        subprocess.run(
            ["git", "push", "win", f"{branch}:{branch}", "--force"],
            cwd=wsl_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error pushing to Windows repo: {e}", file=sys.stderr)
        sys.exit(1)

    # Update Windows working tree using git.exe for proper line endings
    try:
        subprocess.run(
            ["git.exe", "checkout", "--force", "--detach", branch],
            cwd=windows_path,
            check=True,
        )
        print(f"Synced Windows side to '{branch}'")
    except subprocess.CalledProcessError as e:
        print(f"Error updating Windows working tree: {e}", file=sys.stderr)
        sys.exit(1)


def push_command(args):
    """Push the branch to origin via Windows side."""
    relative_path, config = resolve_config()

    wsl_path = config["wsl_path"]
    windows_path = config["windows_path"]
    branch = config["branch"]

    # First sync WSL -> Windows so Windows has the latest
    ensure_win_remote(wsl_path, windows_path)
    try:
        subprocess.run(
            ["git", "push", "win", f"{branch}:{branch}", "--force"],
            cwd=wsl_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error syncing to Windows: {e}", file=sys.stderr)
        sys.exit(1)

    # Push from Windows side using git.exe (Windows remotes/credentials)
    try:
        subprocess.run(
            ["git.exe", "push", "origin", branch],
            cwd=windows_path,
            check=True,
        )
        print(f"Pushed '{branch}' to origin")
    except subprocess.CalledProcessError as e:
        print(f"Error pushing: {e}", file=sys.stderr)
        sys.exit(1)


def pull_command(args):
    """Fetch from origin and update the branch."""
    relative_path, config = resolve_config()

    wsl_path = config["wsl_path"]
    windows_path = config["windows_path"]
    branch = config["branch"]

    # Fetch from origin using git.exe (Windows credentials/remotes)
    try:
        subprocess.run(
            ["git.exe", "fetch", "origin"],
            cwd=windows_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error fetching: {e}", file=sys.stderr)
        sys.exit(1)

    # Update the branch ref on Windows side
    try:
        subprocess.run(
            ["git.exe", "update-ref", f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"],
            cwd=windows_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error updating branch ref: {e}", file=sys.stderr)
        sys.exit(1)

    # Fetch from Windows to WSL
    ensure_win_remote(wsl_path, windows_path)
    try:
        subprocess.run(
            ["git", "fetch", "win", branch],
            cwd=wsl_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error fetching from Windows: {e}", file=sys.stderr)
        sys.exit(1)

    # Fast-forward merge on WSL
    try:
        subprocess.run(
            ["git", "merge", "--ff-only", f"win/{branch}"],
            cwd=wsl_path,
            check=True,
        )
        print(f"Updated '{branch}' from origin")
    except subprocess.CalledProcessError as e:
        print(f"Error merging: {e}", file=sys.stderr)
        sys.exit(1)


def up_command(args):
    """Copy uncommitted changes from WSL to Windows side."""
    cwd = get_git_toplevel() or os.getcwd()

    # Must be on WSL side (lookup by wsl_path)
    result = find_config_by_wsl_path(cwd)
    if result is None:
        print(f"Error: Not a weaseltree-managed WSL path: {cwd}", file=sys.stderr)
        sys.exit(1)

    _, config = result
    windows_path = Path(config["windows_path"])

    # Get list of changed files (modified, added, untracked)
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error running git status: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    lines = result.stdout.splitlines()
    if not lines:
        print("No changes to copy")
        return

    copied = 0
    deleted = 0
    for line in lines:
        if not line:
            continue
        status = line[:2]
        filepath = line[3:]

        # Handle renames (R oldname -> newname)
        if " -> " in filepath:
            filepath = filepath.split(" -> ")[1]

        src = Path(cwd) / filepath
        dst = windows_path / filepath

        # Handle deletions
        if status.strip() == "D":
            if dst.exists():
                dst.unlink()
                print(f"  Deleted: {filepath}")
                deleted += 1
            continue

        if not src.exists() or src.is_dir():
            continue

        # Create parent directories if needed
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Copy file, preserving target line endings if it exists
        try:
            src_content = src.read_bytes()
            # Check if binary (contains null bytes)
            if b'\x00' in src_content:
                dst.write_bytes(src_content)
            else:
                # Text file - check target line endings
                use_crlf = False
                if dst.exists():
                    try:
                        with open(dst, 'rb') as f:
                            chunk = f.read(8192)
                            if b'\r\n' in chunk:
                                use_crlf = True
                    except Exception:
                        pass

                if use_crlf:
                    # Normalize to LF then convert to CRLF
                    src_content = src_content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                    src_content = src_content.replace(b'\n', b'\r\n')

                dst.write_bytes(src_content)
        except Exception:
            # Fallback to simple copy
            shutil.copy2(src, dst)

        print(f"  Copied: {filepath}")
        copied += 1

    print(f"Copied {copied} file(s) to {windows_path}")
    if deleted:
        print(f"Deleted {deleted} file(s)")


def attach_command(args):
    """Put Windows side back on the real branch."""
    relative_path, config = resolve_config()

    windows_path = config["windows_path"]
    branch = config["branch"]

    # Checkout the branch on Windows side
    try:
        subprocess.run(
            ["git.exe", "checkout", branch],
            cwd=windows_path,
            check=True,
        )
        print(f"Windows side now on branch '{branch}'")
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def list_command(args):
    """List all weaseltree-managed directories."""
    config = load_config()
    if not config:
        print("No managed directories")
        return

    for key, entry in config.items():
        if isinstance(entry, dict):
            print(f"{key}:")
            print(f"  branch:  {entry.get('branch', '?')}")
            print(f"  windows: {entry.get('windows_path', '?')}")
            print(f"  wsl:     {entry.get('wsl_path', '?')}")


def run_command(args):
    """Run a command on the Windows side in the corresponding directory."""
    if not args.cmd:
        print("Error: No command specified", file=sys.stderr)
        print("Usage: weaseltree run <command> [args...]", file=sys.stderr)
        sys.exit(1)

    actual_cwd = os.getcwd()
    toplevel = get_git_toplevel() or actual_cwd

    # Try Windows side first (/mnt/c/...)
    relative_path = extract_relative_path(toplevel)
    if relative_path is not None:
        config = load_repo_config(relative_path)
        if config:
            windows_path = config["windows_path"]
        else:
            windows_path = toplevel
    else:
        # Try WSL side (lookup by wsl_path)
        result = find_config_by_wsl_path(toplevel)
        if result is not None:
            _, config = result
            windows_path = config["windows_path"]
        else:
            print(f"Error: Not a weaseltree-managed path: {toplevel}", file=sys.stderr)
            print("Run 'weaseltree clone' first to set up the mapping.", file=sys.stderr)
            sys.exit(1)

    # Preserve subdirectory offset
    subdir = os.path.relpath(actual_cwd, toplevel)
    if subdir != ".":
        windows_path = str(Path(windows_path) / subdir)

    result = subprocess.run(["cmd.exe", "/c"] + args.cmd, cwd=windows_path)
    sys.exit(result.returncode)


def show_status():
    """Show available commands and current repository status."""
    print("weaseltree - WSL git sync helper")
    print()
    print("Commands:")
    print("  link   Link a WSL repo to its Windows counterpart")
    print("  sync   Push WSL commits to Windows side")
    print("  up     Copy uncommitted changes from WSL to Windows")
    print("  push   Push the branch to origin (via Windows)")
    print("  pull   Fetch from origin and update the branch")
    print("  list   List all managed directories")
    print("  attach Put Windows side back on real branch")
    print("  run    Run a command on the Windows side")
    print()

    cwd = os.getcwd()

    # Check if we're in a git repo
    if not os.path.exists(os.path.join(cwd, ".git")):
        print("Status: Not a git repository")
        return

    print("Status:")
    current_branch = get_current_branch()
    if current_branch:
        print(f"  Branch: {current_branch}")
    else:
        print(f"  Branch: (detached HEAD)")

    relative_path = extract_relative_path(cwd)

    # Try Windows side first
    if relative_path is not None:
        print(f"  Relative path: {relative_path}")
        config = load_repo_config(relative_path)
        if config:
            print(f"  Mapped branch: {config['branch']}")
            print(f"  Windows path: {config['windows_path']}")
            wsl_path = config.get('wsl_path')
            if wsl_path and Path(wsl_path).exists():
                print(f"  WSL clone: {wsl_path}")
            else:
                print(f"  WSL clone: (not created)")
        return

    # Try WSL side (lookup by wsl_path)
    result = find_config_by_wsl_path(cwd)
    if result is not None:
        rel_path, config = result
        print(f"  Config key: {rel_path}")
        print(f"  Mapped branch: {config['branch']}")
        print(f"  Windows path: {config['windows_path']}")
        print(f"  WSL clone: {config['wsl_path']}")
        return

    print(f"  Path: {cwd} (not managed by weaseltree)")


def main():
    if is_native_windows():
        print("Error: weaseltree must be run from WSL, not Windows", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="WSL git sync helper")
    subparsers = parser.add_subparsers(dest="command")

    # link subcommand
    link_parser = subparsers.add_parser(
        "link", help="Link a WSL repo to its Windows counterpart"
    )
    link_parser.add_argument(
        "windows_path",
        help="Path to the Windows repo (e.g. /mnt/c/r/myproject)"
    )
    link_parser.set_defaults(func=link_command)

    # sync subcommand
    sync_parser = subparsers.add_parser(
        "sync", help="Push WSL commits to Windows side"
    )
    sync_ahead = sync_parser.add_mutually_exclusive_group()
    sync_ahead.add_argument(
        "--pull", action="store_true",
        help="Merge Windows-side commits into WSL branch before syncing"
    )
    sync_ahead.add_argument(
        "--drop", action="store_true",
        help="Force-push to Windows, discarding any Windows-only commits"
    )
    sync_parser.set_defaults(func=sync_command)

    # push subcommand
    push_parser = subparsers.add_parser(
        "push", help="Push the branch to origin (via Windows)"
    )
    push_parser.set_defaults(func=push_command)

    # up subcommand
    up_parser = subparsers.add_parser(
        "up", help="Copy uncommitted changes from WSL to Windows"
    )
    up_parser.set_defaults(func=up_command)

    # pull subcommand
    pull_parser = subparsers.add_parser(
        "pull", help="Fetch from origin and update the branch"
    )
    pull_parser.set_defaults(func=pull_command)

    # list subcommand
    list_parser = subparsers.add_parser(
        "list", help="List all managed directories"
    )
    list_parser.set_defaults(func=list_command)

    # attach subcommand
    attach_parser = subparsers.add_parser(
        "attach", help="Put Windows side back on real branch"
    )
    attach_parser.set_defaults(func=attach_command)

    # run subcommand
    run_parser = subparsers.add_parser(
        "run", help="Run a command on the Windows side"
    )
    run_parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run")
    run_parser.set_defaults(func=run_command)

    args = parser.parse_args()
    if args.command is None:
        show_status()
    else:
        args.func(args)


if __name__ == "__main__":
    main()
