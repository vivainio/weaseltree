# weaseltree

WSL git worktree helper for working with repositories across Windows and WSL.

## Problem

Git operations on Windows filesystem from WSL are slow. This tool creates a git worktree on the native WSL filesystem, mirroring your Windows repository path structure.

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
3. Work in `~/r/myproject` (fast git operations)
4. Run `weaseltree sync` to update Windows side
5. Use Windows IDE pointing at `C:\r\myproject` (sees latest changes)

## Config

Stored at `%USERPROFILE%\.weaseltree`:

```ini
[r/myproject]
branch = feature-x
windows_path = /mnt/c/r/myproject
```
