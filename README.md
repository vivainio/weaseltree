# weaseltree

Sync git worktrees between fast WSL filesystem and slow Windows filesystem.

## Problem

All filesystem operations from WSL on Windows drives (`/mnt/c/...`) are slow due to the 9P protocol translation layer. This affects everything: builds, git operations, file watchers, IDE indexing.

AI coding tools like Claude Code also benefit significantly from fast filesystem access - file searches, code analysis, and edits are all much faster on native WSL paths.

This tool uses [git worktree](https://git-scm.com/docs/git-worktree) to create a linked working tree on the native WSL filesystem for fast development, while keeping a synchronized copy on Windows for IDE access.

## Install

```bash
pip install -e .
```

## Usage

### Clone (from Windows side)

Navigate to your Windows repository (under `/mnt/c/...`) and run:

```bash
cd /mnt/c/r/myproject
weaseltree clone
```

This will:
1. Detach HEAD on the Windows side
2. Create a worktree at `~/r/myproject` on the same branch
3. Save the config to `~/.weaseltree` (Windows home)

### Sync

Update the Windows side to the latest commit on the branch:

```bash
# From either side
weaseltree sync
```

Run this after making commits on the WSL side to update the Windows worktree.

### Up

Copy uncommitted changes from WSL to Windows (without committing):

```bash
# From WSL side
weaseltree up
```

This copies all modified, added, and untracked files to the Windows worktree. Useful for previewing changes in a Windows IDE before committing.

### Push

Push the branch to origin (from Windows side):

```bash
cd /mnt/c/r/myproject
weaseltree push
```

### Status

Run without arguments to see available commands and current repository status:

```bash
weaseltree
```

## Workflow

1. Start with a repo on Windows: `/mnt/c/r/myproject` on branch `feature-x`
2. Run `weaseltree clone` to create `~/r/myproject` worktree
3. Work in `~/r/myproject` (fast filesystem - builds, git, everything)
4. Run `weaseltree up` to copy uncommitted changes to Windows (for IDE preview)
5. Run `weaseltree sync` after commits to update Windows side
6. Use Windows IDE pointing at `C:\r\myproject` (sees synced changes)

## Config

Stored at `%USERPROFILE%\.weaseltree`:

```ini
[r/myproject]
branch = feature-x
windows_path = /mnt/c/r/myproject
```
