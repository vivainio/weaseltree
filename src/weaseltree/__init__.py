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


def setup_link(wsl_path: str, windows_path: str):
    """Set up the link between a WSL repo and its Windows counterpart."""
    windows_path = str(Path(windows_path).resolve())

    relative_path = extract_relative_path(windows_path)
    if relative_path is None:
        print(f"Error: Windows path must be under /mnt/<drive>/: {windows_path}", file=sys.stderr)
        sys.exit(1)

    if not (Path(windows_path) / ".git").is_dir():
        print(f"Error: Not a git repository: {windows_path}", file=sys.stderr)
        sys.exit(1)

    current_branch = get_current_branch(wsl_path)
    if current_branch is None:
        print("Error: Not on a branch (detached HEAD?)", file=sys.stderr)
        sys.exit(1)

    save_repo_config(relative_path, current_branch, windows_path, wsl_path)
    print(f"Linked {wsl_path} <-> {windows_path}")
    print(f"Saved config to {get_weaseltree_config()}")


def link_command(args):
    """Link a WSL repo to its Windows counterpart."""
    cwd = get_git_toplevel() or os.getcwd()

    if extract_relative_path(cwd) is not None:
        print("Error: Run 'weaseltree link' from the WSL side, not /mnt/", file=sys.stderr)
        sys.exit(1)

    if not (Path(cwd) / ".git").is_dir():
        print(f"Error: Not a git repository: {cwd}", file=sys.stderr)
        sys.exit(1)

    setup_link(cwd, args.windows_path)


def http_to_ssh(url: str) -> str:
    """Convert HTTPS git URL to SSH format.

    https://github.com/user/repo.git -> git@github.com:user/repo.git
    """
    match = re.match(r"^https?://([^/]+)/(.+)$", url)
    if match:
        host = match.group(1)
        path = match.group(2)
        return f"git@{host}:{path}"
    return url


def clone_command(args):
    """Clone the Windows repo's remote to WSL and link them."""
    windows_path = str(Path(args.windows_path).resolve())

    relative_path = extract_relative_path(windows_path)
    if relative_path is None:
        print(f"Error: Windows path must be under /mnt/<drive>/: {windows_path}", file=sys.stderr)
        sys.exit(1)

    if not (Path(windows_path) / ".git").is_dir():
        print(f"Error: Not a git repository: {windows_path}", file=sys.stderr)
        sys.exit(1)

    # Get the remote URL from the Windows repo
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=windows_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error: No 'origin' remote in {windows_path}", file=sys.stderr)
        sys.exit(1)
    remote_url = result.stdout.strip()

    # Convert HTTP to SSH unless overridden
    if args.remote:
        clone_url = args.remote
    else:
        clone_url = http_to_ssh(remote_url)

    if clone_url != remote_url:
        print(f"Remote: {remote_url} -> {clone_url}")

    # Get the current branch from the Windows repo
    branch = get_current_branch(windows_path)
    if branch is None:
        # May be detached from a previous weaseltree version — check config
        config = load_repo_config(relative_path)
        if config:
            branch = config["branch"]
            print(f"Windows repo is detached, using configured branch '{branch}'")
        else:
            print("Error: Windows repo is not on a branch", file=sys.stderr)
            sys.exit(1)

    # Determine WSL target
    if args.target:
        wsl_target = str(Path(args.target).expanduser().resolve())
    else:
        wsl_target = str(Path.home() / relative_path)

    if Path(wsl_target).exists():
        print(f"Error: Target already exists: {wsl_target}", file=sys.stderr)
        print(f"Use 'weaseltree link' to link an existing repo.", file=sys.stderr)
        sys.exit(1)

    # Clone from the real remote (fast)
    Path(wsl_target).parent.mkdir(parents=True, exist_ok=True)
    print(f"Cloning {clone_url} to {wsl_target}...")
    try:
        subprocess.run(
            ["git", "clone", "--branch", branch, clone_url, wsl_target],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error cloning: {e}", file=sys.stderr)
        sys.exit(1)

    # Link the new clone to the Windows repo
    setup_link(wsl_target, windows_path)


def sync_command(args):
    """Push WSL branch to origin and pull on Windows side."""
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

    # Push to origin from WSL
    try:
        subprocess.run(
            ["git", "push", "origin", branch],
            cwd=wsl_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error pushing to origin: {e}", file=sys.stderr)
        sys.exit(1)

    # Checkout and pull on Windows side
    try:
        subprocess.run(
            ["git.exe", "checkout", branch],
            cwd=windows_path,
            check=True,
        )
        subprocess.run(
            ["git.exe", "pull", "--ff-only", "origin", branch],
            cwd=windows_path,
            check=True,
        )
        print(f"Synced Windows side to '{branch}'")
    except subprocess.CalledProcessError as e:
        print(f"Error pulling on Windows side: {e}", file=sys.stderr)
        sys.exit(1)


def push_command(args):
    """Push the branch to origin."""
    relative_path, config = resolve_config()

    wsl_path = config["wsl_path"]
    branch = config["branch"]

    try:
        subprocess.run(
            ["git", "push", "origin", branch],
            cwd=wsl_path,
            check=True,
        )
        print(f"Pushed '{branch}' to origin")
    except subprocess.CalledProcessError as e:
        print(f"Error pushing: {e}", file=sys.stderr)
        sys.exit(1)


def pull_command(args):
    """Pull from origin on both WSL and Windows sides."""
    relative_path, config = resolve_config()

    wsl_path = config["wsl_path"]
    windows_path = config["windows_path"]
    branch = config["branch"]

    # Pull on WSL side
    try:
        subprocess.run(
            ["git", "pull", "--ff-only", "origin", branch],
            cwd=wsl_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error pulling on WSL side: {e}", file=sys.stderr)
        sys.exit(1)

    # Pull on Windows side
    try:
        subprocess.run(
            ["git.exe", "pull", "--ff-only", "origin", branch],
            cwd=windows_path,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error pulling on Windows side: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Updated '{branch}' from origin")


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
    print("  clone  Clone a Windows repo's remote to WSL and link")
    print("  link   Link an existing WSL repo to its Windows counterpart")
    print("  sync   Push to origin and pull on Windows side")
    print("  up     Copy uncommitted changes from WSL to Windows")
    print("  push   Push the branch to origin")
    print("  pull   Pull from origin on both sides")
    print("  list   List all managed directories")
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
        print(f"  Branch: (unknown)")

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

    # clone subcommand
    clone_parser = subparsers.add_parser(
        "clone", help="Clone a Windows repo's remote to WSL and link them"
    )
    clone_parser.add_argument(
        "windows_path",
        help="Path to the Windows repo (e.g. /mnt/c/r/myproject)"
    )
    clone_parser.add_argument(
        "target", nargs="?", default=None,
        help="Target directory for clone (default: ~/relative/path)"
    )
    clone_parser.add_argument(
        "--remote", default=None,
        help="Override the remote URL (default: auto-convert HTTP to SSH)"
    )
    clone_parser.set_defaults(func=clone_command)

    # link subcommand
    link_parser = subparsers.add_parser(
        "link", help="Link an existing WSL repo to its Windows counterpart"
    )
    link_parser.add_argument(
        "windows_path",
        help="Path to the Windows repo (e.g. /mnt/c/r/myproject)"
    )
    link_parser.set_defaults(func=link_command)

    # sync subcommand
    sync_parser = subparsers.add_parser(
        "sync", help="Push to origin and pull on Windows side"
    )
    sync_parser.set_defaults(func=sync_command)

    # push subcommand
    push_parser = subparsers.add_parser(
        "push", help="Push the branch to origin"
    )
    push_parser.set_defaults(func=push_command)

    # up subcommand
    up_parser = subparsers.add_parser(
        "up", help="Copy uncommitted changes from WSL to Windows"
    )
    up_parser.set_defaults(func=up_command)

    # pull subcommand
    pull_parser = subparsers.add_parser(
        "pull", help="Pull from origin on both sides"
    )
    pull_parser.set_defaults(func=pull_command)

    # list subcommand
    list_parser = subparsers.add_parser(
        "list", help="List all managed directories"
    )
    list_parser.set_defaults(func=list_command)

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
