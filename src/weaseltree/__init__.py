import argparse
import configparser
import os
import re
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
    """Get the path to the .weaseltree config file on Windows side."""
    return get_windows_home() / ".weaseltree"


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


def get_current_branch() -> str | None:
    """Get the current git branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        branch = result.stdout.strip()
        if branch != "HEAD":  # Not in detached HEAD state
            return branch
    return None


def save_branch_config(relative_path: str, branch: str):
    """Save branch mapping to Windows ~/.weaseltree config file."""
    config_path = get_weaseltree_config()
    config = configparser.ConfigParser()
    if config_path.exists():
        config.read(config_path)
    if "branches" not in config:
        config["branches"] = {}
    config["branches"][relative_path] = branch
    with open(config_path, "w") as f:
        config.write(f)


def load_branch_config(relative_path: str) -> str | None:
    """Load branch mapping from Windows ~/.weaseltree config file."""
    config_path = get_weaseltree_config()
    config = configparser.ConfigParser()
    if config_path.exists():
        config.read(config_path)
        if "branches" in config and relative_path in config["branches"]:
            return config["branches"][relative_path]
    return None


def clone_command(args):
    cwd = os.getcwd()

    relative_path = extract_relative_path(cwd)
    if relative_path is None:
        print(f"Error: Not under /mnt/<drive>/: {cwd}", file=sys.stderr)
        sys.exit(1)

    wsl_target = Path.home() / relative_path

    # Verify current dir is a git repo
    if not os.path.exists(os.path.join(cwd, ".git")):
        print(f"Error: Not a git repository: {cwd}", file=sys.stderr)
        sys.exit(1)

    if wsl_target.exists():
        print(f"Target already exists: {wsl_target}")
        sys.exit(0)

    # Get current branch before any changes
    current_branch = get_current_branch()
    if current_branch is None:
        print("Error: Not on a branch (detached HEAD?)", file=sys.stderr)
        sys.exit(1)

    # Detach HEAD on Windows side first (to free up the branch)
    try:
        subprocess.run(["git", "checkout", "--detach"], check=True)
        print(f"Detached HEAD on Windows side")
    except subprocess.CalledProcessError as e:
        print(f"Error detaching HEAD: {e}", file=sys.stderr)
        sys.exit(1)

    # Create parent directories for target
    wsl_target.parent.mkdir(parents=True, exist_ok=True)

    # Create the worktree on the same branch
    try:
        subprocess.run(
            ["git", "worktree", "add", str(wsl_target), current_branch],
            check=True,
        )
        print(f"Created worktree at: {wsl_target} on branch '{current_branch}'")
    except subprocess.CalledProcessError as e:
        print(f"Error creating worktree: {e}", file=sys.stderr)
        sys.exit(1)

    # Save branch mapping for later sync
    save_branch_config(relative_path, current_branch)
    print(f"Saved branch mapping to {get_weaseltree_config()}")


def sync_command(args):
    cwd = os.getcwd()

    relative_path = extract_relative_path(cwd)
    if relative_path is None:
        print(f"Error: Not under a drive root: {cwd}", file=sys.stderr)
        sys.exit(1)

    # Load branch from config
    branch = load_branch_config(relative_path)
    if branch is None:
        print(f"Error: No branch mapping found for {relative_path}", file=sys.stderr)
        print(f"Run 'weaseltree clone' first.", file=sys.stderr)
        sys.exit(1)

    # Sync to latest version of the branch (detached)
    try:
        subprocess.run(["git", "checkout", "--detach", branch], check=True)
        print(f"Synced to latest '{branch}'")
    except subprocess.CalledProcessError as e:
        print(f"Error syncing: {e}", file=sys.stderr)
        sys.exit(1)


def push_command(args):
    cwd = os.getcwd()

    relative_path = extract_relative_path(cwd)
    if relative_path is None:
        print(f"Error: Not under a drive root: {cwd}", file=sys.stderr)
        sys.exit(1)

    # Load branch from config
    branch = load_branch_config(relative_path)
    if branch is None:
        print(f"Error: No branch mapping found for {relative_path}", file=sys.stderr)
        print(f"Run 'weaseltree clone' first.", file=sys.stderr)
        sys.exit(1)

    # Push the branch to origin
    try:
        subprocess.run(["git", "push", "origin", branch], check=True)
        print(f"Pushed '{branch}' to origin")
    except subprocess.CalledProcessError as e:
        print(f"Error pushing: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="WSL git worktree helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # clone subcommand
    clone_parser = subparsers.add_parser(
        "clone", help="Create a git worktree mirroring the Windows path"
    )
    clone_parser.set_defaults(func=clone_command)

    # sync subcommand
    sync_parser = subparsers.add_parser(
        "sync", help="Sync Windows side to latest version of the branch"
    )
    sync_parser.set_defaults(func=sync_command)

    # push subcommand
    push_parser = subparsers.add_parser(
        "push", help="Push the WSL branch to origin"
    )
    push_parser.set_defaults(func=push_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
