"""Create a source-only MOSS AI deployment archive with no credentials or images."""
import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

INCLUDE = (
    "server.py", "passenger_wsgi.py", "catalog_match.py", "ai_image_api.py",
    "ai_worker.py", "ai_preflight.py", "benchmark_ai_worker.py",
    "requirements.txt", "requirements-ai-worker.txt",
    "install_ai_worker.sh", "AI_DEPLOYMENT.md", "README.md",
    "export_verified_training.py",
    "diagnostics/hosted_camera_live.py", "diagnostics/server_ai_lookup.py",
    "diagnostics/hosted_live_lookup.py", "diagnostics/fixed_height_preview.py",
    "diagnostics/evidence_replay.py", "diagnostics/read_footer_winrt.ps1",
    "diagnostics/START_AI_CAMERA_TEST.cmd", "diagnostics/START_AI_CAMERA_TEST.ps1",
    "diagnostics/install_ai_camera_test.ps1",
)


def build(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Will not overwrite an existing release archive")
    missing = [name for name in INCLUDE if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing required source files: " + ", ".join(missing))
    checksums = []
    with ZipFile(destination, "x", compression=ZIP_DEFLATED, compresslevel=8) as archive:
        for name in sorted(INCLUDE):
            payload = (source / name).read_bytes()
            if len(payload) > 5 * 1024 * 1024:
                raise ValueError("Unusually large source file: " + name)
            archive.writestr("moss_recognition/" + name, payload)
            checksums.append(hashlib.sha256(payload).hexdigest() + "  " + name)
        archive.writestr("moss_recognition/MANIFEST.sha256", "\n".join(checksums) + "\n")
    return len(INCLUDE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("moss-ai-staging.zip"))
    args = parser.parse_args()
    count = build(Path(__file__).resolve().parent, args.output)
    print(f"Packaged {count} allowlisted files: {args.output}")


if __name__ == "__main__":
    main()
