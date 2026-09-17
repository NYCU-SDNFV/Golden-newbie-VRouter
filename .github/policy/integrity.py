#!/usr/bin/env python3
"""Compare protected files with an instructor-owned manifest."""

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import sys


class ManifestError(ValueError):
    pass


def load_manifest(path):
    entries = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ManifestError(f"{path}:{number}: invalid SHA-256 manifest entry")
        digest, name = match.groups()
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts
                or str(relative) != name or "\\" in name or name in entries):
            raise ManifestError(f"{path}:{number}: unsafe or duplicate protected path")
        entries[name] = digest
    if not entries:
        raise ManifestError(f"{path}: protected-file manifest is empty")
    return entries


def check_integrity(workspace, entries):
    workspace = Path(workspace).resolve()
    failures = []
    for name, expected in entries.items():
        path = workspace / name
        if any(part.is_symlink() for part in [path, *path.parents] if part != workspace):
            failures.append(f"protected path is a symbolic link: {name}")
            continue
        if not path.is_file():
            failures.append(f"protected file is missing: {name}")
            continue
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
        except OSError as exc:
            failures.append(f"protected file cannot be read: {name}: {exc}")
            continue
        if digest.hexdigest() != expected:
            failures.append(f"protected file differs from the required release: {name}")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        entries = load_manifest(args.manifest)
        failures = check_integrity(args.workspace, entries)
    except (OSError, ManifestError) as exc:
        print(f"FAIL: cannot verify protected files: {exc}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print("An older starter release can also cause this. Run make check-update; "
              "merge the instructor update PR or run make update. "
              "Do not edit protected files or their hashes.", file=sys.stderr)
        return 1
    print(f"PASS: protected files match the required release ({len(entries)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
