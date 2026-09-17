#!/usr/bin/env python3
"""Student release checks and lossless, tag-to-tag updates (Python stdlib only).

Publisher API: apply_update(repo, template_url, old_tag, new_tag, branch) returns
True with changes staged on a new branch, or False for an unchanged release.
The caller commits. UpgradeConflict leaves the old HEAD on the update branch
with the index/worktree available for manual resolution. Other failures raise
UpgradeError. Absolute local repository paths are accepted by this API for
offline integration tests; the CLI uses only the metadata's public HTTPS URL.
"""

import argparse
import hashlib
import http.client
import json
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


MARKER = ".lab-release.json"
LOCAL_PROVENANCE = ".lab-local-provenance.json"
MANIFEST = ".github/policy/manifest.sha256"
MAX_METADATA_BYTES = 16 * 1024
NETWORK_TIMEOUT = 10
GIT_TIMEOUT = 120
COAUTHOR = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
DEVELOPMENT = {"schema": 1, "version": "development"}
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
CHANNEL_RE = re.compile(r"(?:newbie|[1-9][0-9]{1,3}-[12])\Z")
TAG_RE = re.compile(r"(?P<channel>newbie|[1-9][0-9]{1,3}-[12])-v(?P<version>[0-9.]+)\Z")
PRESERVED = (".classroom50.yaml", ".github/workflows/autograde.yaml")


class UpgradeError(Exception):
    """An explicit, actionable freshness or update failure."""


class UpgradeConflict(UpgradeError):
    """An update needs manual resolution; its branch still has the old HEAD."""


def version_tuple(value):
    if not isinstance(value, str) or len(value) > 128 or not VERSION_RE.fullmatch(value):
        raise UpgradeError("Invalid release version: expected stable semver MAJOR.MINOR.PATCH.")
    return tuple(int(part) for part in value.split("."))


def validate_metadata(data, *, allow_development=False):
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise UpgradeError("Invalid release metadata: expected schema 1.")
    if allow_development and data == DEVELOPMENT:
        return data
    required = {"schema", "channel", "version", "tag", "template", "source_sha", "minimum_version"}
    if set(data) != required or not all(isinstance(data[key], str) for key in required - {"schema"}):
        raise UpgradeError("Invalid release metadata: missing, unexpected, or non-string fields.")
    channel = data["channel"]
    if not CHANNEL_RE.fullmatch(channel):
        raise UpgradeError("Invalid release channel: expected newbie or a semester such as 115-1.")
    version = version_tuple(data["version"])
    minimum = version_tuple(data["minimum_version"])
    if minimum > version:
        raise UpgradeError("Invalid release metadata: minimum_version exceeds version.")
    if _validate_tag(data["tag"]) != (channel, version):
        raise UpgradeError("Invalid release metadata: tag must match channel and version.")
    # Do not let a changed marker redirect Git toward the private instructor repo.
    if data["template"] != f"NYCU-SDNFV/Golden-{channel}-VRouter":
        raise UpgradeError("Invalid release template: expected the public NYCU-SDNFV channel template.")
    if not re.fullmatch(r"[0-9a-f]{40}", data["source_sha"]):
        raise UpgradeError("Invalid release metadata: source_sha must be 40 lowercase hexadecimal digits.")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpgradeError(f"Invalid release metadata: duplicate field {key!r}.")
        result[key] = value
    return result


def parse_metadata(raw, *, allow_development=False):
    if len(raw) > MAX_METADATA_BYTES:
        raise UpgradeError("Release metadata exceeds the 16 KiB size limit.")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise UpgradeError(f"Cannot parse release metadata: {exc}") from exc
    return validate_metadata(data, allow_development=allow_development)


def _git(repo, *args, check=True, input=None):
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
             "-C", str(repo), *args],
            input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpgradeError(f"Cannot run Git: {exc}. Inspect git status before retrying.") from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise UpgradeError(f"Git {args[0]} failed: {detail}")
    return result


def _blob(repo, revision, path):
    return _git(repo, "show", f"{revision}:{path}").stdout


def _development_tree(repo):
    # These tracked publisher inputs are stripped from every student template.
    paths = (".release.json", "tools/student-build/strip.py")
    return all(
        (repo / path).is_file()
        and _git(repo, "cat-file", "-e", f"HEAD:{path}", check=False).returncode == 0
        for path in paths
    )


def load_local_metadata(repo):
    try:
        with (repo / MARKER).open("rb") as stream:
            raw = stream.read(MAX_METADATA_BYTES + 1)
    except FileNotFoundError as exc:
        raise UpgradeError(
            "This repository has no .lab-release.json. Ask your instructor to migrate this "
            "legacy repository; do not create a marker or guess a base tag."
        ) from exc
    except OSError as exc:
        raise UpgradeError(f"Cannot read {MARKER}: {exc}") from exc
    data = parse_metadata(raw, allow_development=True)
    if data == DEVELOPMENT and not _development_tree(repo):
        raise UpgradeError("Development metadata is only valid in the instructor development tree.")
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpgradeError("Release metadata redirected unexpectedly; ask your instructor to check the template URL.")


def latest_metadata(current):
    validate_metadata(current)
    # Branch URLs can remain cached after a release; each freshness check needs a new cache key.
    url = (f"https://raw.githubusercontent.com/{current['template']}/main/{MARKER}"
           f"?lab3_check={uuid.uuid4().hex}")
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "Lab3-upgrade/1", "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=NETWORK_TIMEOUT) as response:
            raw = response.read(MAX_METADATA_BYTES + 1)
        latest = parse_metadata(raw)
        if (latest["channel"], latest["template"]) != (current["channel"], current["template"]):
            raise UpgradeError("Latest metadata changed channel or template.")
        if version_tuple(latest["version"]) < version_tuple(current["version"]):
            raise UpgradeError("Latest metadata is older than this checkout; release provenance needs instructor review.")
        if latest["version"] == current["version"] and latest != current:
            raise UpgradeError("An immutable release's metadata changed; ask your instructor to investigate.")
        return latest
    except (OSError, urllib.error.URLError, http.client.HTTPException, UpgradeError, ValueError) as exc:
        raise UpgradeError(
            f"Freshness could not be established: {exc} "
            "Check your connection and retry make check-update; contact your instructor if it persists."
        ) from exc


def local_provenance(repo, current):
    path = Path(repo) / LOCAL_PROVENANCE
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_METADATA_BYTES:
            raise UpgradeError("Local materialization provenance exceeds the 16 KiB size limit.")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise UpgradeError(f"Cannot parse local materialization provenance: {exc}") from exc
    required = {"schema", "kind", "published", "channel", "version", "tag", "source_sha"}
    if (not isinstance(data, dict) or set(data) != required
            or type(data.get("schema")) is not int or data.get("schema") != 1
            or data.get("kind") != "local-materialization" or data.get("published") is not False
            or any(data.get(key) != current.get(key)
                   for key in ("channel", "version", "tag", "source_sha"))):
        raise UpgradeError("Local materialization provenance is invalid or inconsistent.")
    try:
        manifest = (Path(repo) / MANIFEST).read_bytes().splitlines()
    except OSError as exc:
        raise UpgradeError(f"Cannot verify local materialization provenance: {exc}") from exc
    expected = hashlib.sha256(raw).hexdigest().encode() + b"  " + LOCAL_PROVENANCE.encode()
    entries = [line for line in manifest if line.endswith(b"  " + LOCAL_PROVENANCE.encode())]
    if entries != [expected]:
        raise UpgradeError("Local materialization provenance is not canonically protected.")
    return data


def check_freshness(repo):
    current = load_local_metadata(repo)
    if current == DEVELOPMENT:
        print("Instructor development tree: release freshness does not apply; no network request made.")
        return True
    if local_provenance(repo, current):
        print(
            "Unpublished local Lab 3 materialization: no online freshness request was made. "
            "Use make test-offline; rematerialize from the private source for updates."
        )
        return True
    latest = latest_metadata(current)
    if version_tuple(current["version"]) < version_tuple(latest["minimum_version"]):
        raise UpgradeError(
            f"Lab3 {current['version']} is too old; {latest['minimum_version']} or newer is required "
            f"(latest {latest['version']}). Commit or stash your work, then run make update. "
            "Merge the resulting instructor/update branch before retrying make test. "
            "Then push your classroom default branch to resubmit; a local update alone "
            "does not update the submitted code."
        )
    if version_tuple(current["version"]) < version_tuple(latest["version"]):
        print(f"Lab3 {current['version']} is supported; optional update {latest['version']} is available via make update.")
    else:
        print(f"Lab3 {current['version']} is current ({current['channel']}).")
    return True


def _validate_tag(tag):
    if isinstance(tag, str):
        match = TAG_RE.fullmatch(tag)
        if match:
            return match["channel"], version_tuple(match["version"])
        if tag.startswith("v"):
            return "newbie", version_tuple(tag[1:])
        if CHANNEL_RE.fullmatch(tag):
            return tag, (0, 1, 0)
    raise UpgradeError(
        "Invalid immutable release tag; expected <channel>-vMAJOR.MINOR.PATCH, "
        "vMAJOR.MINOR.PATCH for newbie, or an initial 0.1.0 channel alias."
    )


def _clean_repo(repo):
    top = _git(repo, "rev-parse", "--show-toplevel").stdout.decode().strip()
    if Path(top).resolve() != repo.resolve():
        raise UpgradeError("Updates must run at the root of the student repository.")
    for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer", "BISECT_LOG"):
        path = _git(repo, "rev-parse", "--git-path", state).stdout.decode().strip()
        if (repo / path).exists():
            raise UpgradeError("Finish or abort the active merge/rebase/cherry-pick/bisect before make update.")
    flags = _git(repo, "ls-files", "-v", "-z").stdout.split(b"\0")
    if any(entry and (entry[:1].islower() or entry[:1] == b"S") for entry in flags):
        raise UpgradeError(
            "Tracked files have assume-unchanged/skip-worktree flags. Disable sparse checkout "
            "and clear those flags, then inspect git status before updating."
        )
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout:
        raise UpgradeError("Working tree is not clean. Commit or stash all changes, including untracked files, before make update.")
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode:
        raise UpgradeError("Check out your student branch before make update; detached HEAD is not supported.")
    return branch.stdout.decode().strip()


def _validate_source(template_url, metadata):
    expected = f"https://github.com/{metadata['template']}.git"
    if template_url == expected:
        return False
    if isinstance(template_url, str) and Path(template_url).is_absolute() and Path(template_url).is_dir():
        return True
    raise UpgradeError(f"Refusing unexpected template source; expected {expected}.")


def _release_at(repo, revision, tag):
    raw = _blob(repo, revision, MARKER)
    metadata = parse_metadata(raw)
    if metadata["tag"] != tag:
        raise UpgradeError(f"Release tag {tag} does not match its metadata.")
    manifest = _blob(repo, revision, MANIFEST)
    expected = hashlib.sha256(raw).hexdigest().encode() + b"  " + MARKER.encode()
    entries = [line for line in manifest.splitlines() if line.endswith(b"  " + MARKER.encode())]
    if entries != [expected]:
        raise UpgradeError(f"Release {tag} does not canonically protect {MARKER} in its integrity manifest.")
    return metadata, raw, manifest


def _github_tree(repo, revision):
    entries = _git(repo, "ls-tree", "-r", "-z", revision, "--", ".github").stdout
    result = {}
    for entry in entries.split(b"\0"):
        if entry:
            identity, path = entry.split(b"\t", 1)
            result[path.decode("utf-8", errors="surrogateescape")] = identity
    return result


def apply_update(repo: Path, template_url: str, old_tag: str, new_tag: str, branch: str) -> bool:
    """Stage a public template's three-way patch; never commit, push, or reset.

    Both immutable tags must carry consistent release metadata and its manifest
    hash. The student's committed marker must match its declared tag. Its manifest
    may already match the target if Classroom has synchronized .github; exact
    target .github objects are not applied twice. Existing branches are never
    reused. Classroom-injected configuration is excluded from the diff.
    See module documentation for conflict semantics.
    """
    repo = Path(repo).resolve()
    old_channel, old_version = _validate_tag(old_tag)
    new_channel, new_version = _validate_tag(new_tag)
    if new_channel != old_channel or new_version < old_version:
        raise UpgradeError("Refusing a cross-channel update or release downgrade.")
    if branch != f"instructor/update-{new_tag}":
        raise UpgradeError(f"Update branch must be instructor/update-{new_tag}.")
    _git(repo, "check-ref-format", "--branch", branch)
    original_branch = _clean_repo(repo)
    current = load_local_metadata(repo)
    if current == DEVELOPMENT:
        raise UpgradeError("The instructor development tree cannot be upgraded as a student release.")
    if current["tag"] not in (old_tag, new_tag):
        raise UpgradeError("Student release metadata does not match the expected old or new tag.")
    local_source = _validate_source(template_url, current)
    resolved_source = _git(repo, "ls-remote", "--get-url", template_url).stdout.decode().strip()
    if resolved_source != template_url:
        raise UpgradeError("Git URL rewriting would change the public template source; remove that rewrite before updating.")
    namespace = f"refs/classroom50/update-{uuid.uuid4().hex}"
    old_ref, new_ref = f"{namespace}/old", f"{namespace}/new"
    try:
        # No origin/upstream changes, default tag imports, or private source fetches.
        specs = [f"refs/tags/{old_tag}:{old_ref}"]
        if new_tag != old_tag:
            specs.append(f"refs/tags/{new_tag}:{new_ref}")
        _git(repo, "-c", "protocol.allow=never", "-c",
             f"protocol.{'file' if local_source else 'https'}.allow=always",
             "-c", "http.followRedirects=false",
             "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
             "--", template_url, *specs)
        if new_tag == old_tag:
            new_ref = old_ref
        for ref in (old_ref, new_ref):
            _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
        for tag, ref in ((old_tag, old_ref), (new_tag, new_ref)):
            local_tag = _git(repo, "show-ref", "--verify", "--hash", f"refs/tags/{tag}", check=False)
            if local_tag.returncode == 0 and local_tag.stdout != _git(repo, "rev-parse", ref).stdout:
                raise UpgradeError(f"Immutable tag {tag} differs from your existing local tag; ask your instructor to investigate.")
        old, old_raw, old_manifest = _release_at(repo, old_ref, old_tag)
        new, new_raw, new_manifest = _release_at(repo, new_ref, new_tag)
        if (old["channel"], old["template"]) != (new["channel"], new["template"]):
            raise UpgradeError("Immutable tags belong to different channels or templates.")
        _validate_source(template_url, old)
        expected, expected_raw = (
            (new, new_raw) if current["tag"] == new_tag
            else (old, old_raw)
        )
        if current != expected or _blob(repo, "HEAD", MARKER) != expected_raw:
            raise UpgradeError("Committed release metadata does not match the immutable template tag; ask your instructor to migrate it.")
        valid_manifests = (new_manifest,) if current["tag"] == new_tag else (old_manifest, new_manifest)
        if _blob(repo, "HEAD", MANIFEST) not in valid_manifests:
            raise UpgradeError(
                "Committed integrity manifest does not match the immutable old or target template tag; "
                "ask your instructor before updating."
            )
        if current["tag"] == new_tag:
            return False
        if _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
            raise UpgradeError(f"Branch {branch} already exists; inspect and merge or rename it before retrying. It was not changed.")
        old_github = _github_tree(repo, old_ref)
        new_github = _github_tree(repo, new_ref)
        head_github = _github_tree(repo, "HEAD")
        # Classroom can sync .github (including additions/deletions) before the
        # student merges the root release marker. Skip only exact target objects.
        already_synced = [
            path for path in old_github.keys() | new_github.keys()
            if old_github.get(path) != new_github.get(path)
            and head_github.get(path) == new_github.get(path)
        ]
        patch = _git(
            repo, "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv",
            "--no-renames", old_ref, new_ref, "--", ".",
            *(f":(top,literal,exclude){path}" for path in (*PRESERVED, *sorted(already_synced))),
        ).stdout
        if not patch:
            return False
        _git(repo, "checkout", "-b", branch)
        result = _git(repo, "apply", "--3way", "--index", "--whitespace=nowarn", "-", input=patch, check=False)
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise UpgradeConflict(
                f"Update needs attention on {branch}; HEAD still points to your original commit. "
                f"Git reported: {detail}\n"
                "Run git status, resolve conflicts without discarding your exercise changes, "
                "then git add the resolved files and git commit. Do not rerun make update over this branch. "
                f"To return to your work without completing the update, preserve any resolutions first, "
                f"then run git restore --source=HEAD --staged --worktree -- . and git switch {shlex.quote(original_branch)}. "
                "No commit or push was made."
            )
        if _blob(repo, "", MARKER) != new_raw or _blob(repo, "", MANIFEST) != new_manifest:
            raise UpgradeConflict(
                f"Update on {branch} did not produce the exact release metadata/manifest. "
                "HEAD is unchanged; inspect git status and contact your instructor before committing."
            )
        return True
    finally:
        for ref in {old_ref, new_ref}:
            _git(repo, "update-ref", "-d", ref)


def update(repo):
    current = load_local_metadata(repo)
    if current == DEVELOPMENT:
        print("Instructor development tree: student updates do not apply; no network request made.")
        return
    if local_provenance(repo, current):
        raise UpgradeError(
            "This is an unpublished local Lab 3 materialization. It has no immutable "
            "public template tags to update from; rematerialize from the private source."
        )
    original_branch = _clean_repo(repo)
    latest = latest_metadata(current)
    if latest["tag"] == current["tag"]:
        print(f"Lab3 {current['version']} is already current; no branch or commit created.")
        return
    # Fail before applying anything if Git cannot identify the student's commit.
    _git(repo, "var", "GIT_AUTHOR_IDENT")
    _git(repo, "var", "GIT_COMMITTER_IDENT")
    branch = f"instructor/update-{latest['tag']}"
    if not apply_update(repo, f"https://github.com/{current['template']}.git",
                        current["tag"], latest["tag"], branch):
        print("Release already applied; no commit created.")
        return
    if parse_metadata(_blob(repo, "", MARKER)) != latest:
        raise UpgradeError(
            f"Latest metadata and immutable tag disagree. Changes are staged on {branch}, "
            "but not committed; contact your instructor and inspect git status."
        )
    try:
        _git(repo, "commit", "-m", f"Update Lab3 to {latest['tag']}\n\n{COAUTHOR}")
    except UpgradeError as exc:
        raise UpgradeError(
            f"{exc}\nChanges remain staged on {branch}; inspect git status, fix your Git "
            "identity/signing configuration, and commit before merging the update branch."
        ) from exc
    print(
        f"Updated safely on {branch}; your exercise changes were merged, not replaced. No push was made.\n"
        f"Review git show, then git switch {shlex.quote(original_branch)} && git merge --ff-only {branch}. "
        f"Run make test, then git push origin {shlex.quote(original_branch)} to resubmit. "
        "If your original branch is not the Classroom default branch, merge it into that branch "
        "and push the default branch too; pushing only the update branch is not a submission. "
        "For an offline checkpoint use make test-offline, then reconnect before pushing."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "update"))
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    try:
        if args.command == "check":
            check_freshness(repo)
        else:
            update(repo)
    except UpgradeError as exc:
        print(f"Lab3: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
