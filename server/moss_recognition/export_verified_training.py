"""Export ONLY operator-approved scans as a private training/evaluation manifest.

This does not claim to retrain PaddleOCR by itself: precise text-box labels
are still required for OCR fine-tuning, and a separate image encoder is needed
for learned artwork matching. All metadata stays in the private data folder.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def export_manifest(data_dir, output_dir):
    data_dir, output_dir = Path(data_dir).resolve(), Path(output_dir).resolve()
    if "public_html" in output_dir.parts:
        raise ValueError("Training exports must remain outside public_html")
    image_dir = data_dir / "scan_images"
    with closing(sqlite3.connect(f"file:{(data_dir / 'moss_recognition.db').as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        approved = db.execute(
            """SELECT id, image_file, verified_product_id, verified_name,
               verified_set, verified_number, reviewed_at FROM scans
               WHERE state='verified' AND verified_product_id IS NOT NULL
               ORDER BY id""").fetchall()
    train, validation, missing = [], [], 0
    for row in approved:
        sid = row["id"]
        if row["image_file"] != sid + ".jpg":
            missing += 1
            continue
        path = image_dir / row["image_file"]
        if not path.is_file() or not all(row[k] for k in
                ("verified_product_id", "verified_name", "verified_set", "verified_number")):
            missing += 1
            continue
        identity = str(row["verified_product_id"])
        bucket = int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16) % 10
        example = {
            "scan_id": sid, "image": str(path), "product_id": identity,
            "name": row["verified_name"], "set": row["verified_set"],
            "collector_number": row["verified_number"],
            "reviewed_at": row["reviewed_at"], "source": "operator_verified"
        }
        (validation if bucket == 0 else train).append(example)
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    for name, records in (("train", train), ("validation", validation)):
        temporary = output_dir / (name + ".jsonl.new")
        with temporary.open("w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(temporary, output_dir / (name + ".jsonl"))
    return {"approved": len(approved), "train": len(train),
            "validation": len(validation), "missing_or_invalid": missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_manifest(args.data_dir, args.output_dir)))


if __name__ == "__main__":
    main()
