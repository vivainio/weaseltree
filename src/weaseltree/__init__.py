import argparse
import configparser
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


def extract_relative_path_from_wsl_home(path: str) -> str | None:
    """Extract relative path from WSL home directory.

    ~/r/foo/bar -> r/foo/bar
    Returns the path only if it's registered in .weaseltree config.
    """
    home = str(Path.home())
    if path.startswith(home + "/"):
        relative = path[len(home) + 1:]
        # Check if this relative path is in the config
        if load_worktree_config(relative) is not None:
            return relative
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


def save_worktree_config(relative_path: str, branch: str, windows_path: str):
    """Save worktree config to Windows ~/.weaseltree config file."""
    config_path = get_weaseltree_config()
    config = configparser.ConfigParser()
    if config_path.exists():
        config.read(config_path)
    # Use relative_path as section name
    if relative_path not in config:
        config[relative_path] = {}
    config[relative_path]["branch"] = branch
    config[relative_path]["windows_path"] = windows_path
    with open(config_path, "w") as f:
        config.write(f)


def load_worktree_config(relative_path: str) -> dict | None:
    """Load worktree config from Windows ~/.weaseltree config file."""
    config_path = get_weaseltree_config()
    config = configparser.ConfigParser()
    if config_path.exists():
        config.read(config_path)
        if relative_path in config:
            return {
                "branch": config[relative_path].get("branch"),
                "windows_path": config[relative_path].get("windows_path"),
            }
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
        # Check if it's already a git worktree
        if not os.path.exists(os.path.join(wsl_target, ".git")):
            print(f"Error: Target exists but is not a git worktree: {wsl_target}", file=sys.stderr)
            sys.exit(1)

        # Get branch from Windows side
        win_branch = get_current_branch()
        if win_branch is None:
            # Windows is detached, get branch from WSL worktree
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(wsl_target),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or result.stdout.strip() == "HEAD":
                print(f"Error: Could not determine branch from WSL worktree", file=sys.stderr)
                sys.exit(1)
            current_branch = result.stdout.strip()
            print(f"WSL worktree already exists: {wsl_target}")
        else:
            # Windows is on a branch - switch WSL worktree to it
            current_branch = win_branch
            try:
                subprocess.run(
                    ["git", "checkout", current_branch],
                    cwd=str(wsl_target),
                    check=True,
                )
                print(f"Switched WSL worktree to branch '{current_branch}'")
            except subprocess.CalledProcessError as e:
                print(f"Error switching WSL worktree: {e}", file=sys.stderr)
                sys.exit(1)

            # Detach HEAD on Windows side
            try:
                subprocess.run(["git", "checkout", "--detach"], check=True)
                print(f"Detached HEAD on Windows side")
            except subprocess.CalledProcessError as e:
                print(f"Error detaching HEAD: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        # Get current branch before any changes
        current_branch = get_current_branch()
        if current_branch is None:
            print("Error: Not on a branch (detached HEAD?)", file=sys.stderr)
            sys.exit(1)

        # Detach HEAD on Windows side (to free up the branch)
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

    # Save worktree config for later sync
    save_worktree_config(relative_path, current_branch, cwd)
    print(f"Saved config to {get_weaseltree_config()}")


def sync_command(args):
    cwd = os.getcwd()

    # Try Windows side first (/mnt/c/...)
    relative_path = extract_relative_path(cwd)
    if relative_path is not None:
        config = load_worktree_config(relative_path)
        if config is None:
            print(f"Error: No config found for {relative_path}", file=sys.stderr)
            print(f"Run 'weaseltree clone' first.", file=sys.stderr)
            sys.exit(1)

        # Verify we're in detached HEAD before using --force
        if get_current_branch() is not None:
            print(f"Error: Windows side is not in detached HEAD. Run 'weaseltree clone' to fix.", file=sys.stderr)
            sys.exit(1)

        # Sync to latest version of the branch using git.exe for proper Windows line endings
        try:
            subprocess.run(["git.exe", "checkout", "--force", "--detach", config["branch"]], check=True)
            print(f"Synced to latest '{config['branch']}'")
        except subprocess.CalledProcessError as e:
            print(f"Error syncing: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Try WSL side (~/...)
    relative_path = extract_relative_path_from_wsl_home(cwd)
    if relative_path is not None:
        config = load_worktree_config(relative_path)
        windows_path = config["windows_path"]

        # Verify Windows side is in detached HEAD before using --force
        result = subprocess.run(
            ["git.exe", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=windows_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip() != "HEAD":
            print(f"Error: Windows side is not in detached HEAD. Run 'weaseltree clone' to fix.", file=sys.stderr)
            sys.exit(1)

        # Sync Windows side using git.exe for proper Windows line endings
        try:
            subprocess.run(
                ["git.exe", "checkout", "--force", "--detach", config["branch"]],
                cwd=windows_path,
                check=True,
            )
            print(f"Synced Windows side ({windows_path}) to latest '{config['branch']}'")
        except subprocess.CalledProcessError as e:
            print(f"Error syncing Windows side: {e}", file=sys.stderr)
            sys.exit(1)
        return

    print(f"Error: Not a weaseltree-managed path: {cwd}", file=sys.stderr)
    sys.exit(1)


def push_command(args):
    cwd = os.getcwd()

    # Try Windows side first (/mnt/c/...)
    relative_path = extract_relative_path(cwd)
    if relative_path is not None:
        config = load_worktree_config(relative_path)
        if config is None:
            print(f"Error: No config found for {relative_path}", file=sys.stderr)
            print(f"Run 'weaseltree clone' first.", file=sys.stderr)
            sys.exit(1)

        # Push the branch to origin
        try:
            subprocess.run(["git", "push", "origin", config["branch"]], check=True)
            print(f"Pushed '{config['branch']}' to origin")
        except subprocess.CalledProcessError as e:
            print(f"Error pushing: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Try WSL side (~/...)
    relative_path = extract_relative_path_from_wsl_home(cwd)
    if relative_path is not None:
        config = load_worktree_config(relative_path)
        windows_path = config["windows_path"]

        # Push from Windows side using git.exe (uses Windows remotes/credentials)
        try:
            subprocess.run(
                ["git.exe", "push", "origin", config["branch"]],
                cwd=windows_path,
                check=True,
            )
            print(f"Pushed '{config['branch']}' to origin (via Windows)")
        except subprocess.CalledProcessError as e:
            print(f"Error pushing: {e}", file=sys.stderr)
            sys.exit(1)
        return

    print(f"Error: Not a weaseltree-managed path: {cwd}", file=sys.stderr)
    sys.exit(1)


def up_command(args):
    """Copy uncommitted changes from WSL to Windows side."""
    cwd = os.getcwd()

    # Must be on WSL side
    relative_path = extract_relative_path_from_wsl_home(cwd)
    if relative_path is None:
        print(f"Error: Not a weaseltree-managed WSL path: {cwd}", file=sys.stderr)
        sys.exit(1)

    config = load_worktree_config(relative_path)
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

        if not src.exists():
            continue

        # Create parent directories if needed
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Copy file
        shutil.copy2(src, dst)
        print(f"  Copied: {filepath}")
        copied += 1

    print(f"Copied {copied} file(s) to {windows_path}")
    if deleted:
        print(f"Deleted {deleted} file(s)")


def show_status():
    """Show available commands and current repository status."""
    print("weaseltree - WSL git worktree helper")
    print()
    print("Commands:")
    print("  clone  Create a git worktree mirroring the Windows path")
    print("  sync   Sync Windows side to latest version of the branch")
    print("  up     Copy uncommitted changes from WSL to Windows")
    print("  push   Push the WSL branch to origin")
    print()

    cwd = os.getcwd()
    relative_path = extract_relative_path(cwd)

    # Check if we're in a git repo
    if not os.path.exists(os.path.join(cwd, ".git")):
        print("Status: Not a git repository")
        return

    print("Status:")
    current_branch = get_current_branch()
    if current_branch:
        print(f"  Branch: {current_branch}")
    else:
        print("  Branch: (detached HEAD)")

    # Try Windows side first
    if relative_path is not None:
        print(f"  Relative path: {relative_path}")
        config = load_worktree_config(relative_path)
        if config:
            print(f"  Mapped branch: {config['branch']}")
            print(f"  Windows path: {config['windows_path']}")
        wsl_target = Path.home() / relative_path
        if wsl_target.exists():
            print(f"  WSL worktree: {wsl_target}")
        else:
            print(f"  WSL worktree: (not created)")
        return

    # Try WSL side
    relative_path = extract_relative_path_from_wsl_home(cwd)
    if relative_path is not None:
        print(f"  Relative path: {relative_path}")
        config = load_worktree_config(relative_path)
        if config:
            print(f"  Mapped branch: {config['branch']}")
            print(f"  Windows path: {config['windows_path']}")
        return

    print(f"  Path: {cwd} (not managed by weaseltree)")


def main():
    parser = argparse.ArgumentParser(description="WSL git worktree helper")
    subparsers = parser.add_subparsers(dest="command")

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

    # up subcommand
    up_parser = subparsers.add_parser(
        "up", help="Copy uncommitted changes from WSL to Windows"
    )
    up_parser.set_defaults(func=up_command)

    args = parser.parse_args()
    if args.command is None:
        show_status()
    else:
        args.func(args)


if __name__ == "__main__":
    main()
