#!/usr/bin/env python3
"""Self-test for the sequence-integrity machinery — no API calls, no network.

Run this BEFORE pointing the tool at your real photo folder to confirm that:
  * images sort by filename and group into consecutive sets of 3,
  * trailing/odd images are detected as leftovers,
  * the vision role-validation correctly accepts a clean front/back/runout
    group and rejects a drifted one (a missed or extra shot).

    python selftest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image

from main import discover_images, group_images, sort_images
from vision import validate_group_roles


def _make_placeholder(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (64, 64), color).save(path, format="PNG")


def test_grouping_and_leftovers() -> None:
    # Three albums' worth of placeholder shots, named out of order on disk to
    # prove the filename sort puts them back in sequence.
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        names = [
            "IMG_0003.png", "IMG_0001.png", "IMG_0002.png",   # album 1
            "IMG_0006.png", "IMG_0004.png", "IMG_0005.png",   # album 2
            "IMG_0009.png", "IMG_0007.png", "IMG_0008.png",   # album 3
        ]
        for i, name in enumerate(names):
            _make_placeholder(folder / name, (i * 20 % 256, 100, 150))

        ordered = sort_images(discover_images(folder))
        assert [p.name for p in ordered] == sorted(names), "filename sort failed"

        groups, leftovers = group_images(ordered)
        assert len(groups) == 3, f"expected 3 groups, got {len(groups)}"
        assert leftovers == [], "expected no leftovers for a clean 9-image set"
        # First group must be the first three in sorted order.
        assert [p.name for p in groups[0]] == [
            "IMG_0001.png", "IMG_0002.png", "IMG_0003.png",
        ]
        print("✓ grouping: 9 images → 3 clean groups, correct order")


def test_leftover_detection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for i in range(7):  # 7 images: 2 complete groups + 1 stray
            _make_placeholder(folder / f"IMG_{i:04d}.png", (i * 30 % 256, 80, 80))
        groups, leftovers = group_images(sort_images(discover_images(folder)))
        assert len(groups) == 2, f"expected 2 groups, got {len(groups)}"
        assert len(leftovers) == 1, f"expected 1 leftover, got {len(leftovers)}"
        print("✓ leftover detection: 7 images → 2 groups + 1 flagged stray")


def test_role_validation() -> None:
    # A clean group has exactly one of each.
    assert validate_group_roles(("front", "back", "runout")) is True
    assert validate_group_roles(("runout", "front", "back")) is True

    # Drift from a MISSED shot: the window slides and you get a duplicate role.
    assert validate_group_roles(("front", "runout", "front")) is False
    # Drift from an EXTRA shot: two of the same role inside one window.
    assert validate_group_roles(("front", "front", "back")) is False
    # Missing a role entirely.
    assert validate_group_roles(("front", "back", "back")) is False
    print("✓ role validation: clean group accepted, drifted groups rejected")


def main() -> int:
    tests = [
        test_grouping_and_leftovers,
        test_leftover_detection,
        test_role_validation,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"✗ {test.__name__}: {exc}")
    print()
    if failed:
        print(f"{failed} test(s) FAILED")
        return 1
    print("All self-tests passed. Sequence integrity check is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
