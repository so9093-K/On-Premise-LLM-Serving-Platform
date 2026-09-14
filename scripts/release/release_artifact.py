from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

MANIFEST_NAME = "RELEASE_MANIFEST.json"
PROVENANCE_NAME = "RELEASE_PROVENANCE.json"
SCHEMA_VERSION = 1
CANONICAL_MODES = {"100644": 0o644, "100755": 0o755}
EXCLUDE_TREE_DIRS = {
    ".agents",
    ".claude",
    ".codex",
    ".cursor",
    ".git",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "model_cache",
}
EXCLUDE_TOP_LEVEL_DIRS = {
    ".github",
    ".other",
    "build",
    "dist",
    "env",
    "logs",
    "model_cache",
    "models",
    "outputs",
    "run",
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".secret", ".pem", ".key")
EXCLUDE_FILE_PATTERNS = (".env", ".env.*")
_ALLOWED_MANIFEST_MODES = {"0644", "0755"}


class ReleaseArtifactError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.PIPE
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ReleaseArtifactError(
            f"release source must be a readable Git working tree: {root}"
        ) from exc


def _tracked_source_state(root: Path) -> str:
    """Return state of inputs that can affect this tracked-file release payload."""
    for args in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode == 1:
            return "dirty"
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ReleaseArtifactError(
                f"could not inspect tracked source state in {root}: {detail}"
            )
    return "clean"


def _safe_env_examples(root: Path) -> set[str]:
    # Remote verification intentionally stays stdlib-only. YAML is only needed while
    # resolving release inputs from a source checkout.
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - development environment owns PyYAML
        raise ReleaseArtifactError(
            "PyYAML is required to resolve release inputs; run make setup-dev"
        ) from exc

    contract_path = root / "configs" / "env_contract.yaml"
    try:
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseArtifactError(f"could not read {contract_path}") from exc
    if not isinstance(contract, dict):
        raise ReleaseArtifactError("configs/env_contract.yaml must contain a mapping")
    examples = contract.get("env_examples")
    safe = set(examples) if isinstance(examples, dict) else set()
    if not safe:
        raise ReleaseArtifactError(
            "configs/env_contract.yaml must declare env_examples for release materialization"
        )
    return safe


def _skip_dir(parts: tuple[str, ...], name: str) -> bool:
    top = parts[0] if parts else name
    return (
        parts[:2] == ("reports", "runtime")
        or name.endswith(".egg-info")
        or top in EXCLUDE_TOP_LEVEL_DIRS
        or name in EXCLUDE_TREE_DIRS
        or any(part in EXCLUDE_TREE_DIRS or part.endswith(".egg-info") for part in parts)
    )


def _skip_file(parts: tuple[str, ...], name: str, safe_env_examples: set[str]) -> bool:
    top = parts[0] if parts else name
    if top in EXCLUDE_TOP_LEVEL_DIRS:
        return True
    if any(
        part in EXCLUDE_TREE_DIRS or part.endswith(".egg-info") for part in parts[:-1]
    ):
        return True
    if len(parts) >= 2 and parts[0] == "reports" and parts[1] == "runtime":
        return True
    if name in safe_env_examples:
        return False
    if name.endswith(EXCLUDE_SUFFIXES):
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_FILE_PATTERNS)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(raw_path: str) -> PurePosixPath:
    path = PurePosixPath(raw_path)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise ReleaseArtifactError(f"unsafe release path: {raw_path!r}")
    return path


def _tracked_modes(root: Path) -> dict[str, int]:
    output = _git(root, "ls-files", "-sz")
    tracked: dict[str, int] = {}
    for entry in filter(None, output.decode("utf-8").split("\0")):
        metadata, separator, raw_path = entry.partition("\t")
        if not separator:
            raise ReleaseArtifactError(f"unexpected git ls-files entry: {entry!r}")
        git_mode = metadata.split(" ", 1)[0]
        if git_mode not in CANONICAL_MODES:
            raise ReleaseArtifactError(
                f"unsupported git mode {git_mode} for {raw_path!r}; "
                "release payload accepts only regular tracked files"
            )
        _validate_relative_path(raw_path)
        tracked[raw_path] = CANONICAL_MODES[git_mode]
    return tracked


def _release_paths(root: Path) -> list[tuple[str, int]]:
    safe_env_examples = _safe_env_examples(root)
    selected: list[tuple[str, int]] = []
    for raw_path, mode in sorted(_tracked_modes(root).items()):
        rel = PurePosixPath(raw_path)
        parts = tuple(rel.parts)
        if any(
            _skip_dir(tuple(parts[: index + 1]), dirname)
            for index, dirname in enumerate(parts[:-1])
        ):
            continue
        if _skip_file(parts, rel.name, safe_env_examples):
            continue
        source_file = root.joinpath(*parts)
        if not source_file.is_file():
            # A dirty local package may contain a tracked deletion. The manifest describes
            # the materializable bytes while source.tracked_state records that divergence.
            continue
        selected.append((raw_path, mode))
    return selected


def _payload_digest(files: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in files:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["mode"].encode("ascii"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry["size"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_manifest(source_root: Path, *, version: str) -> dict[str, Any]:
    root = source_root.resolve()
    revision = _git(root, "rev-parse", "HEAD").decode("utf-8").strip()
    files: list[dict[str, Any]] = []
    for raw_path, mode in _release_paths(root):
        source_file = root / raw_path
        files.append(
            {
                "path": raw_path,
                "mode": f"{mode:04o}",
                "size": source_file.stat().st_size,
                "sha256": _sha256_file(source_file),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "source": {
            "revision": revision,
            "tracked_state": _tracked_source_state(root),
        },
        "payload_sha256": _payload_digest(files),
        "file_modes": {"regular": "0644", "executable": "0755"},
        "files": files,
    }


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _provenance_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    source = manifest["source"]
    return {
        "version": manifest["version"],
        "source_revision": source["revision"],
        # Backward-compatible field name; semantics are now explicitly the tracked
        # release input state because untracked files cannot affect this artifact.
        "source_state": source["tracked_state"],
        "payload_sha256": manifest["payload_sha256"],
        "file_modes": manifest["file_modes"],
        "derived_from": MANIFEST_NAME,
    }


def _write_metadata(destination: Path, manifest: dict[str, Any]) -> None:
    manifest_path = destination / MANIFEST_NAME
    manifest_path.write_bytes(_manifest_bytes(manifest))
    manifest_path.chmod(0o644)

    provenance_path = destination / PROVENANCE_NAME
    provenance_path.write_text(
        json.dumps(
            _provenance_projection(manifest),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    provenance_path.chmod(0o644)


def _validate_entry(entry: Any) -> tuple[str, PurePosixPath, str, int, str]:
    if not isinstance(entry, dict):
        raise ReleaseArtifactError("release manifest file entry must be an object")
    raw_path = entry.get("path")
    mode = entry.get("mode")
    size = entry.get("size")
    digest = entry.get("sha256")
    if not isinstance(raw_path, str):
        raise ReleaseArtifactError("release manifest entry path must be a string")
    rel = _validate_relative_path(raw_path)
    if mode not in _ALLOWED_MANIFEST_MODES:
        raise ReleaseArtifactError(f"invalid release mode for {raw_path}: {mode!r}")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ReleaseArtifactError(f"invalid size for {raw_path}")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ReleaseArtifactError(f"invalid sha256 for {raw_path}")
    return raw_path, rel, mode, size, digest


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseArtifactError(f"invalid or missing {MANIFEST_NAME}: {path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        version = manifest.get("schema_version") if isinstance(manifest, dict) else None
        raise ReleaseArtifactError(
            f"unsupported {MANIFEST_NAME} schema_version: {version}"
        )
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ReleaseArtifactError("release manifest source must be an object")
    if not isinstance(source.get("revision"), str) or not source["revision"]:
        raise ReleaseArtifactError("release manifest source.revision must be non-empty")
    if source.get("tracked_state") not in {"clean", "dirty"}:
        raise ReleaseArtifactError("release manifest source.tracked_state must be clean or dirty")
    return manifest


def materialize(source_root: Path, destination: Path, manifest: dict[str, Any]) -> None:
    source = source_root.resolve()
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    files = manifest.get("files")
    if not isinstance(files, list):
        raise ReleaseArtifactError("release manifest files must be a list")
    seen: set[str] = set()
    for entry in files:
        raw_path, rel, mode, expected_size, expected_hash = _validate_entry(entry)
        if raw_path in seen:
            raise ReleaseArtifactError(f"duplicate release path: {raw_path}")
        seen.add(raw_path)
        source_file = source.joinpath(*rel.parts)
        if not source_file.is_file() or source_file.is_symlink():
            raise ReleaseArtifactError(f"release source file disappeared: {raw_path}")
        if source_file.stat().st_size != expected_size or _sha256_file(source_file) != expected_hash:
            raise ReleaseArtifactError(
                f"release source changed after manifest resolution: {raw_path}"
            )
        target = destination.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        target.chmod(int(mode, 8))

    _write_metadata(destination, manifest)
    verify_materialized(destination)


def verify_materialized(root: Path) -> dict[str, Any]:
    release_root = root.resolve()
    manifest = _load_manifest(release_root)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ReleaseArtifactError("release manifest files must be a list")

    expected_paths: set[str] = set()
    normalized_entries: list[dict[str, Any]] = []
    for entry in files:
        raw_path, rel, mode, expected_size, expected_hash = _validate_entry(entry)
        if raw_path in expected_paths:
            raise ReleaseArtifactError(f"duplicate release path: {raw_path}")
        expected_paths.add(raw_path)
        target = release_root.joinpath(*rel.parts)
        if not target.is_file() or target.is_symlink():
            raise ReleaseArtifactError(f"release payload file missing or non-regular: {raw_path}")
        actual_mode = stat.S_IMODE(target.stat().st_mode)
        if actual_mode != int(mode, 8):
            raise ReleaseArtifactError(
                f"release mode mismatch for {raw_path}: {actual_mode:04o} != {mode}"
            )
        if target.stat().st_size != expected_size or _sha256_file(target) != expected_hash:
            raise ReleaseArtifactError(f"release payload mismatch for {raw_path}")
        normalized_entries.append(entry)

    actual_paths: set[str] = set()
    for path in release_root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(release_root).as_posix()
        if rel in {MANIFEST_NAME, PROVENANCE_NAME}:
            continue
        if path.is_symlink():
            raise ReleaseArtifactError(f"unexpected symlink in immutable release payload: {rel}")
        actual_paths.add(rel)
    if actual_paths != expected_paths:
        raise ReleaseArtifactError(
            "release payload file set differs from manifest: "
            f"missing={sorted(expected_paths - actual_paths)[:5]}, "
            f"unexpected={sorted(actual_paths - expected_paths)[:5]}"
        )

    calculated_payload = _payload_digest(normalized_entries)
    if calculated_payload != manifest.get("payload_sha256"):
        raise ReleaseArtifactError("release manifest payload_sha256 does not match file entries")

    for metadata_name in (MANIFEST_NAME, PROVENANCE_NAME):
        metadata_path = release_root / metadata_name
        if not metadata_path.is_file() or metadata_path.is_symlink():
            raise ReleaseArtifactError(f"release metadata missing: {metadata_name}")
        if stat.S_IMODE(metadata_path.stat().st_mode) != 0o644:
            raise ReleaseArtifactError(f"release metadata mode must be 0644: {metadata_name}")
    try:
        provenance = json.loads((release_root / PROVENANCE_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseArtifactError(f"invalid {PROVENANCE_NAME}") from exc
    if provenance != _provenance_projection(manifest):
        raise ReleaseArtifactError(f"{PROVENANCE_NAME} is not derived from {MANIFEST_NAME}")
    return manifest


def _write_manifest_output(manifest: dict[str, Any], output: str) -> None:
    content = _manifest_bytes(manifest)
    if output == "-":
        sys.stdout.buffer.write(content)
        return
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o644)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve, materialize, and verify canonical release payloads."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("--source", type=Path, required=True)
    manifest_parser.add_argument("--version", required=True)
    manifest_parser.add_argument("--output", default="-")

    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--source", type=Path, required=True)
    materialize_parser.add_argument("--destination", type=Path, required=True)
    materialize_parser.add_argument("--version", required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "manifest":
            manifest = build_manifest(args.source, version=args.version)
            _write_manifest_output(manifest, args.output)
        elif args.command == "materialize":
            manifest = build_manifest(args.source, version=args.version)
            materialize(args.source, args.destination, manifest)
            print(manifest["payload_sha256"])
        elif args.command == "verify":
            manifest = verify_materialized(args.root)
            print(manifest["payload_sha256"])
        else:  # pragma: no cover
            raise AssertionError(args.command)
    except ReleaseArtifactError as exc:
        print(f"[release] ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
