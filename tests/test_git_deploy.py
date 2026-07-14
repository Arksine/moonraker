# Unit tests for the update_manager git_repo deployment helper
#
# Copyright (C) 2026  Aleksei Sviridkin <f@lex.la>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from moonraker.components.update_manager.git_deploy import GitRepo


def make_repo(
    branch_lines: list[str],
    remotes: str = "origin",
    tracking_remote: str | None = None,
    git_remote: str = "?",
    git_branch: str = "?",
) -> GitRepo:
    # Build a bare GitRepo without running __init__ and stub the async git
    # helpers that _find_current_branch depends on, so the branch parsing
    # logic can be exercised in isolation.
    repo = GitRepo.__new__(GitRepo)
    repo.alias = "klipper"
    repo.git_remote = git_remote
    repo.git_branch = git_branch
    repo.head_detached = False
    repo.branches = []
    repo.list_branches = AsyncMock(return_value=branch_lines)
    repo.remote = AsyncMock(return_value=remotes)
    repo.config_get = AsyncMock(return_value=tracking_remote)
    return repo


@pytest.mark.asyncio
async def test_find_current_branch_on_branch() -> None:
    repo = make_repo(["* master", "  dev"], tracking_remote="origin")
    await repo._find_current_branch()
    assert repo.head_detached is False
    assert repo.git_branch == "master"
    assert repo.git_remote == "origin"
    assert repo.branches == ["master", "dev"]
    repo.config_get.assert_awaited_once_with("branch.master.remote")


@pytest.mark.asyncio
async def test_find_current_branch_detached_at_remote_branch() -> None:
    # git spells out the remote branch in the ref, so it is recovered.
    repo = make_repo(["* (HEAD detached at origin/master)", "  master"])
    await repo._find_current_branch()
    assert repo.head_detached is True
    assert repo.git_branch == "master"
    assert repo.git_remote == "origin"
    repo.config_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_current_branch_detached_at_tag() -> None:
    # A detached checkout on a bare tag carries no remote in the ref.  With no
    # previously tracked remote the value stays "?" -- it is not inferred.
    repo = make_repo(["* (HEAD detached at v0.13.0)", "  master"])
    await repo._find_current_branch()
    assert repo.head_detached is True
    assert repo.git_remote == "?"
    repo.config_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_current_branch_no_branch() -> None:
    # git renders some detached states as "(no branch)".  This must be treated
    # as detached, never as a literal branch name (which would build the
    # invalid key "branch.(no branch).remote").
    repo = make_repo(["* (no branch)"])
    await repo._find_current_branch()
    assert repo.head_detached is True
    assert repo.git_remote == "?"
    assert repo.branches == []
    repo.config_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_current_branch_no_branch_keeps_previous_tracking() -> None:
    # When a remote/branch was previously detected they are kept, mirroring the
    # existing detached-HEAD behavior.  No inference, no crash.
    repo = make_repo(
        ["* (no branch)"], git_remote="origin", git_branch="master"
    )
    await repo._find_current_branch()
    assert repo.head_detached is True
    assert repo.git_remote == "origin"
    assert repo.git_branch == "master"
    repo.config_get.assert_not_awaited()
