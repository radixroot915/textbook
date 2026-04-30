"""
Vault deduplication cleanup.

Scans saved .txt files, finds near-duplicates via simhash, and removes the
shorter of each pair. Rebuilds fingerprints.txt from survivors.

Usage:
    python cleanup.py                    # dry run, all topics
    python cleanup.py --topic welding    # dry run, one topic
    python cleanup.py --go               # actually delete
    python cleanup.py --topic welding --go
"""
import os
import sys
import argparse
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import VAULT_ROOT, HASH_PATH, MIN_TEXT_LENGTH, SIMHASH_THRESHOLD

SIMHASH_BITS = 64
THRESHOLD = SIMHASH_THRESHOLD
MIN_LENGTH = MIN_TEXT_LENGTH


def _simhash(text: str) -> int:
    v = [0] * SIMHASH_BITS
    features = [text[i:i + 6] for i in range(len(text) - 5)]
    for feature in features:
        h = zlib.adler32(feature.encode("utf-8")) & 0xFFFFFFFF
        h64 = (h << 32) | (h ^ 0x55555555)
        for i in range(SIMHASH_BITS):
            v[i] += 1 if (h64 >> i) & 1 else -1
    fp = 0
    for i in range(SIMHASH_BITS):
        if v[i] > 0:
            fp |= (1 << i)
    return fp


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def scan_vault(topic: str | None) -> list[str]:
    if topic:
        dirs = [os.path.join(VAULT_ROOT, topic)]
    else:
        if not os.path.exists(VAULT_ROOT):
            return []
        dirs = [
            os.path.join(VAULT_ROOT, d)
            for d in os.listdir(VAULT_ROOT)
            if os.path.isdir(os.path.join(VAULT_ROOT, d))
        ]
    files = []
    for d in dirs:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".txt"):
                    files.append(os.path.join(d, f))
    return files


def find_duplicates(files: list[str]) -> tuple[list[str], list[str]]:
    # Sort largest first — always keep the richer file when a pair is found
    files = sorted(files, key=os.path.getsize, reverse=True)

    kept: list[str] = []
    removed: list[str] = []
    fingerprints: list[tuple[int, str]] = []   # (hash, path)

    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            print(f"  [!] Could not read {path}: {e}")
            continue

        size = len(text)

        if size < MIN_LENGTH:
            kept.append(path)
            continue

        fp = _simhash(text)
        is_dup = any(_hamming(fp, stored_fp) <= THRESHOLD for stored_fp, _ in fingerprints)

        if is_dup:
            removed.append(path)
        else:
            fingerprints.append((fp, path))
            kept.append(path)

    return kept, removed


def rebuild_fingerprints(kept: list[str]):
    fps = []
    for path in kept:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            if len(text) >= MIN_LENGTH:
                fps.append(_simhash(text))
        except Exception:
            continue
    with open(HASH_PATH, "w", encoding="utf-8") as f:
        for fp in fps:
            f.write(f"{fp:016x}\n")
    return len(fps)


def main():
    parser = argparse.ArgumentParser(description="Vault deduplication cleanup")
    parser.add_argument("--topic", help="Limit to one topic subdirectory")
    parser.add_argument("--go", action="store_true", help="Actually delete (default is dry run)")
    args = parser.parse_args()

    files = scan_vault(args.topic)
    if not files:
        print("No .txt files found in vault.")
        return

    print(f"Scanning {len(files)} files...")
    kept, removed = find_duplicates(files)

    total_bytes = sum(os.path.getsize(p) for p in removed if os.path.exists(p))

    print(f"\n  Kept:    {len(kept)} files")
    print(f"  Remove:  {len(removed)} duplicates ({total_bytes / 1024:.1f} KB recoverable)")

    if removed:
        print("\nDuplicates:")
        for p in removed:
            size = os.path.getsize(p) if os.path.exists(p) else 0
            print(f"  {os.path.relpath(p, VAULT_ROOT)}  ({size / 1024:.1f} KB)")

    if not args.go:
        print("\n[DRY RUN] Pass --go to delete and rebuild fingerprints.txt")
        return

    deleted = 0
    for p in removed:
        try:
            os.remove(p)
            deleted += 1
        except Exception as e:
            print(f"  [!] Could not delete {p}: {e}")

    fp_count = rebuild_fingerprints(kept)
    print(f"\nDeleted {deleted} files. Rebuilt fingerprints.txt with {fp_count} entries.")


if __name__ == "__main__":
    main()
