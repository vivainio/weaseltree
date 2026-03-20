# weaseltree

Sync git repos between fast WSL filesystem and slow Windows filesystem.

## Problem

All filesystem operations from WSL on Windows drives (`/mnt/c/...`) are slow due to the 9P protocol translation layer. This affects everything: builds, git operations, file watchers, IDE indexing.

AI coding tools like Claude Code also benefit significantly from fast filesystem access - file searches, code analysis, and edits are all much faster on native WSL paths.

This tool creates a full clone on the native WSL filesystem for fast development, while keeping a synchronized copy on Windows for Windows-native tools. Both sides stay on the branch and sync via origin.

## Install

```bash
pip install -e .
```

## Usage

### Clone

Clone a Windows repo's remote to WSL and link them:

```bash
weaseltree clone /mnt/c/r/myproject
```

This will:
1. Read the remote URL and branch from the Windows repo
2. Convert HTTP to SSH URL automatically (override with `--remote <url>`)
3. Clone from the real remote to `~/r/myproject` (fast, no 9P)
4. Save the config to `~/.weaseltree.json` (Windows home)

You can specify a custom target directory:

```bash
weaseltree clone /mnt/c/r/myproject ~/projects/myproject
```

### Link

If you already have both repos cloned independently, link them:

```bash
cd ~/r/myproject
weaseltree link /mnt/c/r/myproject
```

### Sync

Push WSL commits to origin and pull on Windows side:

```bash
# From either side
weaseltree sync
```

Run this after making commits on the WSL side. Pushes to origin, then pulls on the Windows side so both repos are in sync.

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

### Pull

Pull from origin on both WSL and Windows sides:

```bash
# From either side
weaseltree pull
```

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

1. Have a repo on Windows: `/mnt/c/r/myproject`
2. Run `weaseltree clone /mnt/c/r/myproject` to clone via SSH to `~/r/myproject`
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
