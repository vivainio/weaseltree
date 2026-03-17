# weaseltree

Sync git repos between fast WSL filesystem and slow Windows filesystem.

## Problem

All filesystem operations from WSL on Windows drives (`/mnt/c/...`) are slow due to the 9P protocol translation layer. This affects everything: builds, git operations, file watchers, IDE indexing.

AI coding tools like Claude Code also benefit significantly from fast filesystem access - file searches, code analysis, and edits are all much faster on native WSL paths.

This tool creates a full clone on the native WSL filesystem for fast development, while keeping a synchronized copy on Windows for Windows-native tools. The WSL clone has a `win` remote pointing to the Windows repo, enabling simple `git push`/`git fetch` based sync.

## Install

```bash
pip install -e .
```

## Usage

### Link (from WSL side)

You have two repos cloned independently (from the real remote). Link them:

```bash
cd ~/r/myproject
weaseltree link /mnt/c/r/myproject
```

This will:
1. Add a `win` remote on the WSL repo pointing to the Windows repo
2. Detach HEAD on the Windows side
3. Save the config to `~/.weaseltree.json` (Windows home)

### Sync

Push WSL commits to the Windows side:

```bash
# From either side
weaseltree sync
```

Run this after making commits on the WSL side. Uses `git push win` to transfer commits, then `git.exe checkout` so Windows-side git settings (like `core.autocrlf`) are honored.

If the Windows side has commits that aren't on the WSL branch, you'll be prompted to merge, drop, or abort:

```bash
weaseltree sync --pull   # merge Windows commits into WSL branch first
weaseltree sync --drop   # force-push, discarding Windows-only commits
```

### Up

Copy uncommitted changes from WSL to Windows (without committing):

```bash
# From WSL side
weaseltree up
```

This copies all modified, added, and untracked files to the Windows repo. Useful when you need Windows tools to see uncommitted changes.

### Push

Push the branch to origin:

```bash
# From either side
weaseltree push
```

Syncs WSL -> Windows first, then uses `git.exe push` so Windows-side git remotes and credentials are used.

### Pull

Fetch from origin and update the branch:

```bash
# From either side
weaseltree pull
```

Uses `git.exe` for fetch (Windows credentials), then propagates to the WSL clone via `git fetch win`.

### Attach

Hand off to the Windows side by putting it back on the real branch:

```bash
# From either side
weaseltree attach
```

This checks out the branch on Windows so you can work there with normal git. Run `weaseltree sync` to switch back to WSL-side development.

### Switching Branches

To switch to a different branch:

```bash
# In the WSL clone
git checkout other-branch
weaseltree sync
```

The `sync` command automatically detects when the WSL branch has changed and updates the config accordingly.

### Status

Run without arguments to see available commands and current repository status:

```bash
weaseltree
```

## Workflow

1. Clone the repo on both sides: `git clone <url>` on Windows and WSL
2. Run `cd ~/r/myproject && weaseltree link /mnt/c/r/myproject`
3. Work in `~/r/myproject` (fast filesystem - builds, git, everything)
4. Run `weaseltree up` to copy uncommitted changes to Windows
5. Run `weaseltree sync` after commits to update Windows side
6. Access `C:\r\myproject` with Windows-native tools as needed

## Config

Stored at `%USERPROFILE%\.weaseltree.json`:

```json
{
  "r/myproject": {
    "branch": "feature-x",
    "windows_path": "/mnt/c/r/myproject",
    "wsl_path": "/home/user/r/myproject"
  }
}
```
