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


def detach_head():
    """Detach HEAD by writing commit SHA directly to .git/HEAD.

    This is much faster than `git checkout --detach` because it
    bypasses the working tree machinery.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to get current commit")
    commit_sha = result.stdout.strip()
    with open(".git/HEAD", "w") as f:
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


def save_worktree_config(relative_path: str, branch: str, windows_path: str, wsl_path: str):
    """Save worktree config to Windows ~/.weaseltree.json config file."""
    config = load_config()
    config[relative_path] = {
        "branch": branch,
        "windows_path": windows_path,
        "wsl_path": wsl_path,
    }
    save_config(config)


def load_worktree_config(relative_path: str) -> dict | None:
    """Load worktree config from Windows ~/.weaseltree.json config file."""
    config = load_config()
    return config.get(relative_path)


def find_config_by_wsl_path(wsl_path: str) -> tuple[str, dict] | None:
    """Find worktree config by WSL path. Returns (relative_path, config) or None."""
    config = load_config()
    for key, entry in config.items():
        if isinstance(entry, dict) and entry.get("wsl_path") == wsl_path:
            return (key, entry)
    return None


def find_config_by_branch(branch: str) -> tuple[str, dict] | None:
    """Find worktree config by branch name. Returns (relative_path, config) or None."""
    config = load_config()
    for key, entry in config.items():
        if isinstance(entry, dict) and entry.get("branch") == branch:
            return (key, entry)
    return None


def find_config_by_windows_path(windows_path: str) -> tuple[str, dict] | None:
    """Find worktree config by Windows path. Returns (relative_path, config) or None."""
    config = load_config()
    for key, entry in config.items():
        if isinstance(entry, dict) and entry.get("windows_path") == windows_path:
            return (key, entry)
    return None


def get_main_repo_from_worktree() -> str | None:
    """Get the main repo path from a worktree's .git file.

    In a worktree, .git is a file containing: gitdir: /path/to/main/.git/worktrees/name
    Returns the main repo path (e.g., /mnt/c/r/foo) or None.
    """
    git_path = Path(".git")
    if not git_path.exists() or git_path.is_dir():
        return None  # Not a worktree (or is main repo)

    try:
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir = content[8:]  # Remove "gitdir: " prefix
            # gitdir looks like: /mnt/c/r/foo/.git/worktrees/branch-name
            # We want: /mnt/c/r/foo
            if "/.git/worktrees/" in gitdir:
                main_git = gitdir.split("/.git/worktrees/")[0]
                return main_git
    except Exception:
        pass
    return None


def remove_stale_worktree(main_repo: str, current_path: str):
    """Remove stale worktree entries that point to non-existent paths.

    Args:
        main_repo: Path to the main git repository
        current_path: Current worktree path (won't be removed)
    """
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=main_repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return

    current_wt_path = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_wt_path = line[9:]
        elif line == "" and current_wt_path:
            # End of entry - check if path exists
            if current_wt_path != current_path and not Path(current_wt_path).exists():
                # Stale entry - remove it
                remove_result = subprocess.run(
                    ["git", "worktree", "remove", "--force", current_wt_path],
                    cwd=main_repo,
                    capture_output=True,
                    text=True,
                )
                if remove_result.returncode == 0:
                    print(f"Removed stale worktree: {current_wt_path}")
            current_wt_path = None


def clone_command(args):
    cwd = os.getcwd()

    relative_path = extract_relative_path(cwd)
    if relative_path is None:
        print(f"Error: Not under /mnt/<drive>/: {cwd}", file=sys.stderr)
        sys.exit(1)

    # Use custom target if specified, otherwise mirror the path structure
    if args.target:
        wsl_target = Path(args.target).expanduser().resolve()
    else:
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
                detach_head()
                print("Detached HEAD on Windows side")
            except Exception as e:
                print(f"Error detaching HEAD: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        # Get current branch before any changes
        current_branch = get_current_branch()
        if current_branch is None:
            print("Error: Not on a branch (detached HEAD?)", file=sys.stderr)
            sys.exit(1)

        # Check if branch is already checked out in another worktree (not cwd)
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
        )
        current_wt_path = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_wt_path = line[9:]
            elif line == f"branch refs/heads/{current_branch}":
                # Skip if it's the current directory (we'll detach it)
                if current_wt_path != cwd:
                    print(f"Error: Branch '{current_branch}' is already checked out at:", file=sys.stderr)
                    print(f"  {current_wt_path}", file=sys.stderr)
                    print(f"Remove it first: git worktree remove {current_wt_path}", file=sys.stderr)
                    sys.exit(1)

        # Detach HEAD on Windows side (to free up the branch)
        try:
            detach_head()
            print("Detached HEAD on Windows side")
        except Exception as e:
            print(f"Error detaching HEAD: {e}", file=sys.stderr)
            sys.exit(1)

        # Create parent directories for target
        wsl_target.parent.mkdir(parents=True, exist_ok=True)

        # Create the worktree on the same branch
        print(f"Creating worktree at {wsl_target}...")
        try:
            subprocess.run(
                ["git", "worktree", "add", str(wsl_target), current_branch],
                check=True,
            )
            print(f"Created worktree on branch '{current_branch}'")
        except subprocess.CalledProcessError as e:
            print(f"Error creating worktree: {e}", file=sys.stderr)
            sys.exit(1)

    # Save worktree config for later sync
    save_worktree_config(relative_path, current_branch, cwd, str(wsl_target))
    print(f"Saved config to {get_weaseltree_config()}")


def fix_moved_worktree(cwd: str, branch: str) -> tuple[str, dict] | None:
    """Fix config when a worktree has moved. Returns (relative_path, config) or None."""
    # Determine if we're on Windows or WSL side
    relative_path = extract_relative_path(cwd)

    if relative_path is None:
        # WSL side - use worktree .git file to find main repo (most reliable)
        main_repo = get_main_repo_from_worktree()
        if not main_repo:
            print("Error: Could not determine main repo from worktree", file=sys.stderr)
            return None

        main_relative = extract_relative_path(main_repo)
        if not main_relative:
            print(f"Error: Main repo not under /mnt/<drive>/: {main_repo}", file=sys.stderr)
            return None

        print(f"Found main repo: {main_repo}")

        # Check for stale worktree entries and remove them
        remove_stale_worktree(main_repo, cwd)

        # Look up config by windows_path (not branch - avoids ambiguity)
        result = find_config_by_windows_path(main_repo)
        if result:
            old_key, old_config = result
            old_wsl_path = old_config.get("wsl_path")
            if old_wsl_path != cwd:
                print(f"  WSL path: {old_wsl_path} -> {cwd}")
            if old_config.get("branch") != branch:
                print(f"  Branch: {old_config.get('branch')} -> {branch}")
            print(f"Updated config for {old_key}")
        else:
            print(f"Created config for {main_relative}")

        new_config = {
            "branch": branch,
            "windows_path": main_repo,
            "wsl_path": cwd,
        }
        save_worktree_config(main_relative, branch, main_repo, cwd)

        # Repair git worktree links
        repair_result = subprocess.run(
            ["git", "worktree", "repair"],
            capture_output=True,
            text=True,
        )
        if repair_result.returncode == 0:
            if repair_result.stderr.strip():
                print(f"Repaired worktree: {repair_result.stderr.strip()}")
            else:
                print("Worktree links OK")

        return (main_relative, new_config)

    # Windows side - look up by branch (less common case for --fix)
    result = find_config_by_branch(branch)
    if result is None:
        return None

    old_key, old_config = result
    old_wsl_path = old_config.get("wsl_path")
    old_windows_path = old_config.get("windows_path")

    new_windows_path = cwd
    new_wsl_path = old_wsl_path  # Keep old WSL path
    new_key = relative_path

    print(f"Found config for branch '{branch}':")
    print(f"  Old key: {old_key}")
    if new_key != old_key:
        print(f"  New key: {new_key}")
    if new_windows_path != old_windows_path:
        print(f"  Windows: {old_windows_path} -> {new_windows_path}")

    # Update config
    config = load_config()
    if new_key != old_key:
        del config[old_key]
    config[new_key] = {
        "branch": branch,
        "windows_path": new_windows_path,
        "wsl_path": new_wsl_path,
    }
    save_config(config)
    print(f"Updated config")

    # Repair git worktree links
    if new_wsl_path and Path(new_wsl_path).exists():
        repair_result = subprocess.run(
            ["git", "worktree", "repair"],
            cwd=new_wsl_path,
            capture_output=True,
            text=True,
        )
        if repair_result.returncode == 0:
            if repair_result.stderr.strip():
                print(f"Repaired worktree: {repair_result.stderr.strip()}")
            else:
                print("Worktree links OK")
        else:
            print(f"Warning: git worktree repair failed: {repair_result.stderr}", file=sys.stderr)

    return (new_key, config[new_key])


def sync_command(args):
    cwd = os.getcwd()

    # Try Windows side first (/mnt/c/...)
    relative_path = extract_relative_path(cwd)
    if relative_path is not None:
        config = load_worktree_config(relative_path)
        if config is None:
            if getattr(args, 'fix', False):
                # Try to find config by branch and fix it
                branch = get_current_branch()
                if branch:
                    result = fix_moved_worktree(cwd, branch)
                    if result:
                        relative_path, config = result
                    else:
                        print(f"Error: No config found for branch '{branch}'", file=sys.stderr)
                        sys.exit(1)
                else:
                    print(f"Error: Not on a branch, cannot fix", file=sys.stderr)
                    sys.exit(1)
            else:
                print(f"Error: No config found for {relative_path}", file=sys.stderr)
                print(f"Run 'weaseltree clone' first, or use --fix if the repo moved.", file=sys.stderr)
                sys.exit(1)

        # Verify we're in detached HEAD before using --force
        if get_current_branch() is not None:
            print(f"Error: Windows side is not in detached HEAD. Run 'weaseltree clone' to fix.", file=sys.stderr)
            sys.exit(1)

        # Check if WSL worktree branch has changed
        wsl_path = config.get("wsl_path")
        if wsl_path and Path(wsl_path).exists():
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=wsl_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                current_branch = result.stdout.strip()
                if current_branch != "HEAD" and current_branch != config["branch"]:
                    print(f"Branch changed: {config['branch']} -> {current_branch}")
                    config["branch"] = current_branch
                    save_worktree_config(relative_path, current_branch, config["windows_path"], wsl_path)

        # Sync to latest version of the branch using git.exe for proper Windows line endings
        try:
            subprocess.run(["git.exe", "checkout", "--force", "--detach", config["branch"]], check=True)
            print(f"Synced to latest '{config['branch']}'")
        except subprocess.CalledProcessError as e:
            print(f"Error syncing: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Try WSL side (lookup by wsl_path)
    result = find_config_by_wsl_path(cwd)
    if result is None and getattr(args, 'fix', False):
        # Try to find config by branch and fix it
        branch = get_current_branch()
        if branch:
            result = fix_moved_worktree(cwd, branch)
        else:
            print(f"Error: Not on a branch, cannot fix", file=sys.stderr)
            sys.exit(1)

    if result is not None:
        relative_path, config = result
        windows_path = config["windows_path"]

        # Verify Windows side is in detached HEAD before using --force
        check = subprocess.run(
            ["git.exe", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=windows_path,
            capture_output=True,
            text=True,
        )
        if check.returncode == 0 and check.stdout.strip() != "HEAD":
            print(f"Error: Windows side is not in detached HEAD. Run 'weaseltree clone' to fix.", file=sys.stderr)
            sys.exit(1)

        # Check if current branch has changed
        current_branch = get_current_branch()
        if current_branch and current_branch != config["branch"]:
            print(f"Branch changed: {config['branch']} -> {current_branch}")
            config["branch"] = current_branch
            save_worktree_config(relative_path, current_branch, windows_path, cwd)

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
    print(f"Run 'weaseltree clone' first, or use --fix if the repo moved.", file=sys.stderr)
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

    # Try WSL side (lookup by wsl_path)
    result = find_config_by_wsl_path(cwd)
    if result is not None:
        _, config = result
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


def pull_command(args):
    """Fetch from origin and update the branch."""
    cwd = os.getcwd()

    # Try Windows side first (/mnt/c/...)
    relative_path = extract_relative_path(cwd)
    if relative_path is not None:
        config = load_worktree_config(relative_path)
        if config is None:
            print(f"Error: No config found for {relative_path}", file=sys.stderr)
            print(f"Run 'weaseltree clone' first.", file=sys.stderr)
            sys.exit(1)

        wsl_path = config.get("wsl_path")
        if not wsl_path or not Path(wsl_path).exists():
            print(f"Error: WSL worktree not found: {wsl_path}", file=sys.stderr)
            sys.exit(1)

        # Fetch from origin using git.exe (Windows credentials/remotes)
        try:
            subprocess.run(["git.exe", "fetch", "origin"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error fetching: {e}", file=sys.stderr)
            sys.exit(1)

        # Update the branch in WSL worktree (fast-forward merge)
        try:
            subprocess.run(
                ["git", "merge", "--ff-only", f"origin/{config['branch']}"],
                cwd=wsl_path,
                check=True,
            )
            print(f"Updated '{config['branch']}' from origin")
        except subprocess.CalledProcessError as e:
            print(f"Error merging: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Try WSL side (lookup by wsl_path)
    result = find_config_by_wsl_path(cwd)
    if result is not None:
        _, config = result
        windows_path = config["windows_path"]

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

        # Update the branch in WSL worktree (fast-forward merge)
        try:
            subprocess.run(
                ["git", "merge", "--ff-only", f"origin/{config['branch']}"],
                check=True,
            )
            print(f"Updated '{config['branch']}' from origin")
        except subprocess.CalledProcessError as e:
            print(f"Error merging: {e}", file=sys.stderr)
            sys.exit(1)
        return

    print(f"Error: Not a weaseltree-managed path: {cwd}", file=sys.stderr)
    sys.exit(1)


def up_command(args):
    """Copy uncommitted changes from WSL to Windows side."""
    cwd = os.getcwd()

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

        if not src.exists():
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

    cwd = os.getcwd()

    # Try Windows side first (/mnt/c/...)
    relative_path = extract_relative_path(cwd)
    if relative_path is not None:
        config = load_worktree_config(relative_path)
        if config:
            windows_path = config["windows_path"]
        else:
            windows_path = cwd
    else:
        # Try WSL side (lookup by wsl_path)
        result = find_config_by_wsl_path(cwd)
        if result is not None:
            _, config = result
            windows_path = config["windows_path"]
        else:
            print(f"Error: Not a weaseltree-managed path: {cwd}", file=sys.stderr)
            print("Run 'weaseltree clone' first to set up the mapping.", file=sys.stderr)
            sys.exit(1)

    result = subprocess.run(["cmd.exe", "/c"] + args.cmd, cwd=windows_path)
    sys.exit(result.returncode)


def show_status():
    """Show available commands and current repository status."""
    print("weaseltree - WSL git worktree helper")
    print()
    print("Commands:")
    print("  clone  Create a git worktree mirroring the Windows path")
    print("  sync   Sync Windows side to latest version of the branch")
    print("  up     Copy uncommitted changes from WSL to Windows")
    print("  push   Push the WSL branch to origin")
    print("  pull   Fetch from origin and update the branch")
    print("  list   List all managed directories")
    print("  run    Run a command on the Windows side")
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
            wsl_path = config.get('wsl_path')
            if wsl_path and Path(wsl_path).exists():
                print(f"  WSL worktree: {wsl_path}")
            else:
                print(f"  WSL worktree: (not created)")
        return

    # Try WSL side (lookup by wsl_path)
    result = find_config_by_wsl_path(cwd)
    if result is not None:
        rel_path, config = result
        print(f"  Config key: {rel_path}")
        print(f"  Mapped branch: {config['branch']}")
        print(f"  Windows path: {config['windows_path']}")
        print(f"  WSL worktree: {config['wsl_path']}")
        return

    print(f"  Path: {cwd} (not managed by weaseltree)")


def main():
    parser = argparse.ArgumentParser(description="WSL git worktree helper")
    subparsers = parser.add_subparsers(dest="command")

    # clone subcommand
    clone_parser = subparsers.add_parser(
        "clone", help="Create a git worktree mirroring the Windows path"
    )
    clone_parser.add_argument(
        "target", nargs="?", default=None,
        help="Target directory for worktree (default: ~/relative/path)"
    )
    clone_parser.set_defaults(func=clone_command)

    # sync subcommand
    sync_parser = subparsers.add_parser(
        "sync", help="Sync Windows side to latest version of the branch"
    )
    sync_parser.add_argument(
        "--fix", action="store_true",
        help="Fix config if repo has moved (uses branch name to find entry)"
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
