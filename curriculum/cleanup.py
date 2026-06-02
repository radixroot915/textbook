"""
vault_cleanup — post-compile vault pruner.

Called after TextbookCompiler.compile(). Deletes all collected source files
(used and unused) and flags zero-lexicon-hit files as junk sources in
knowledge_map.json so future runs skip them at the coordinator level.

Fingerprint files are kept so the bouncer still prevents re-downloading
already-seen content.
"""

import os
import json
import logging

log = logging.getLogger(__name__)


def vault_cleanup(
    vault_path: str,
    topic: str,
    file_index: dict,
    all_used_sources: set[str],
    map_path: str,
) -> dict:
    """
    Parameters
    ----------
    vault_path      : path to vault/<topic>/ directory
    topic           : topic slug
    file_index      : {filename: FileEntry} from the compiler — must have .lexicon_hits
    all_used_sources: set of filenames that contributed to at least one chapter
    map_path        : path to knowledge_map.json

    Returns a summary dict with counts for logging.
    """
    unused = [f for f in file_index if f not in all_used_sources]
    used   = [f for f in file_index if f in all_used_sources]

    # --- Flag zero-hit unused files as junk before deleting ---
    junk_identifiers = []
    for fname in unused:
        entry = file_index[fname]
        if not entry.lexicon_hits:
            # Filename format: VOL_<node>_<id_slug>.txt
            # id_slug is the last 12 chars of the identifier/URL — enough to log
            junk_identifiers.append(fname)

    if junk_identifiers:
        _record_junk(topic, junk_identifiers, map_path)

    # --- Delete all vault source files ---
    deleted = 0
    errors = 0
    for fname in list(file_index.keys()):
        fpath = os.path.join(vault_path, fname)
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
                deleted += 1
        except Exception as e:
            log.warning(f"[cleanup] Could not delete {fname}: {e}")
            errors += 1

    summary = {
        "deleted_used": len(used),
        "deleted_unused": len(unused),
        "flagged_junk": len(junk_identifiers),
        "total_deleted": deleted,
        "errors": errors,
    }

    log.info(
        f"[cleanup] Deleted {deleted} vault files "
        f"({len(used)} used, {len(unused)} unused) | "
        f"{len(junk_identifiers)} flagged as junk | "
        f"{errors} errors"
    )
    return summary


def _record_junk(topic: str, filenames: list[str], map_path: str):
    """Append filename slugs to knowledge_map.json junk_sources list."""
    try:
        if os.path.exists(map_path):
            with open(map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        topic_data = data.setdefault(topic, {})
        junk_list: list = topic_data.setdefault("junk_sources", [])

        added = 0
        for fname in filenames:
            slug = fname.replace(".txt", "")
            if slug not in junk_list:
                junk_list.append(slug)
                added += 1

        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        log.info(f"[cleanup] Recorded {added} new junk slugs in knowledge_map")
    except Exception as e:
        log.warning(f"[cleanup] Failed to write junk_sources to map: {e}")
