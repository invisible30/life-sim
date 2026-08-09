"""Tests for issue #18 — showcase files are not in output/

The README points users to docs/showcase/ for the live demo. output/ should
be fully gitignored (no whitelist) so local runs never collide with committed
showcase files.
"""
import os
import subprocess
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_showcase_lives_in_docs():
    """docs/showcase/ 应该存在, 含 README + compare.html + biography.html"""
    assert os.path.exists(os.path.join(ROOT, "docs/showcase"))
    assert os.path.exists(os.path.join(ROOT, "docs/showcase/README.md"))
    assert os.path.exists(os.path.join(ROOT, "docs/showcase/compare.html"))
    assert os.path.exists(os.path.join(ROOT, "docs/showcase/biography.html"))


def test_output_dir_no_seed_subdirs():
    """output/ 不应该有 seed42/ 之类 (issue #18 改的)"""
    seed_dirs = [
        d for d in os.listdir(os.path.join(ROOT, "output"))
        if d.startswith("seed")
    ]
    assert seed_dirs == [], f"output/ has seed dirs: {seed_dirs}"


def test_output_compare_html_not_committed():
    """output/compare.html 不在 git 跟踪 (现在搬到 docs/showcase/)"""
    result = subprocess.run(
        ["git", "ls-files", "output/compare.html"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", \
        f"output/compare.html should not be tracked: {result.stdout}"


def test_docs_showcase_tracked():
    """docs/showcase/* 应该在 git 跟踪"""
    result = subprocess.run(
        ["git", "ls-files", "docs/showcase/"],
        cwd=ROOT, capture_output=True, text=True,
    )
    tracked = result.stdout.strip().split("\n")
    assert "docs/showcase/compare.html" in tracked
    assert "docs/showcase/biography.html" in tracked
    assert "docs/showcase/README.md" in tracked


def test_readme_points_to_docs_showcase():
    """README 的 demo 链接应该指向 docs/showcase/, 不是 output/"""
    with open(os.path.join(ROOT, "README.md")) as f:
        content = f.read()
    # 至少出现一次 docs/showcase/ 链接
    assert "docs/showcase/compare.html" in content
    assert "docs/showcase/biography.html" in content
    # 不应该再有指向 output/ 的 demo 链接 (除了注释说'本地跑会到 output/')
    # 简单 sanity: 找一个非注释的 'output/compare.html'
    bad = []
    for line in content.split("\n"):
        if "output/compare.html" in line or "output/seed42" in line:
            # 允许带 '# 仓库自带的 showcase 在 docs/' 注释的行
            if "docs/showcase" in line or "覆盖" in line or "!" in line[:5]:
                continue
            bad.append(line)
    assert bad == [], f"README still points to output/: {bad}"
