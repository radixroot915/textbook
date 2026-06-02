import os
import re
import sys
import json
import logging
import asyncio
import traceback
import config
from llm.ollama_client import health_check
from agents.coordinator import Coordinator


def _inject_gap_nodes(topic: str, gap_nodes: list, log):
    """Write gap nodes into knowledge_map.json as pending so the next
    coordinator.bootstrap() picks them up automatically."""
    if not gap_nodes:
        return
    try:
        if os.path.exists(config.MAP_PATH):
            with open(config.MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        topic_data = data.setdefault(topic, {})
        nodes_data = topic_data.setdefault("nodes", {})
        scores = topic_data.setdefault("frontier_scores", {})

        added = 0
        for node in gap_nodes:
            if node not in nodes_data:
                nodes_data[node] = {
                    "status": "pending",
                    "files": [],
                    "discovery": "gap_loop",
                }
                scores[node] = scores.get(node, 0) + 2
                added += 1
            elif nodes_data[node].get("status") == "stalled":
                # Re-open stalled nodes that the gap analysis flagged
                nodes_data[node]["status"] = "pending"
                scores[node] = scores.get(node, 0) + 1
                added += 1

        with open(config.MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        log.info(f"[LOOP] Injected {added} gap nodes into frontier")
    except Exception as e:
        log.error(f"[LOOP] Failed to inject gap nodes: {e}")


def _setup_logging(topic: str) -> logging.Logger:
    log_path = os.path.join(config.BASE_DIR, f"{topic}_run.log")
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(message)s",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
    else:
        if not any(
            isinstance(h, logging.FileHandler)
            and h.baseFilename == os.path.abspath(log_path)
            for h in root.handlers
        ):
            root.addHandler(logging.FileHandler(log_path, encoding="utf-8"))
    return logging.getLogger("harvester")


def run_harvest(
    topic: str,
    min_files: int = 100,
    max_iterations: int = 5,
    max_cycles: int = 1,
    skip_compile: bool = False,
    hub_urls: list[str] | None = None,
    log: logging.Logger | None = None,
) -> dict:
    """Unified harvest loop — cycles, quality gate, watchdog, fingerprint
    reset, gap-node injection, deep-dive expansion. Single source of truth
    for what a 'full harvest' means; called by both `main()` and
    `workflow.cmd_harvest()`.

    Returns: dict with cycles_run, files, final_quality, gap_nodes.
    """
    if log is None:
        log = logging.getLogger("harvester")

    log.info(
        f"START topic={topic} min_files={min_files} "
        f"max_iter={max_iterations} max_cycles={max_cycles}"
    )

    if not health_check():
        log.warning(
            "Ollama not accessible — LLM bootstrap and expansion disabled; "
            "running with fallback nodes only."
        )
    else:
        log.info("Ollama OK")

    prev_file_count = 0
    prev_quality = None
    from curriculum.quality_gate import evaluate, log_report, has_improved
    from watchdog import wd
    wd.start_run(topic)
    curriculum_dir = os.path.join(config.VAULT_ROOT, topic, "curriculum")

    cycles_run = 0
    final_quality = None
    final_gap_nodes: list = []

    for cycle in range(1, max_cycles + 1):
        cycles_run = cycle
        wd.set_cycle(cycle)
        wd.emit("loop", "cycle_start", cycle=cycle)
        if max_cycles > 1:
            log.info(f"\n{'='*60}")
            log.info(f"  HARVEST CYCLE {cycle}/{max_cycles}")
            log.info(f"{'='*60}")

        coordinator = Coordinator(topic, min_files, max_iterations, skip_compile=skip_compile, hub_urls=hub_urls)
        try:
            gap_nodes = asyncio.run(coordinator.run())
        except Exception as e:
            log.error(f"Coordinator crashed on cycle {cycle}: {e}")
            log.error(traceback.format_exc())
            break

        final_gap_nodes = gap_nodes

        # Check how many files we now have
        vault_path = os.path.join(config.VAULT_ROOT, topic)
        current_file_count = len(
            [f for f in os.listdir(vault_path) if f.endswith(".txt")]
        ) if os.path.exists(vault_path) else 0

        log.info(
            f"[LOOP] Cycle {cycle} complete — "
            f"{current_file_count} files total "
            f"(+{current_file_count - prev_file_count} this cycle) | "
            f"{len(gap_nodes)} gap nodes"
        )

        # Quality gate — evaluate the textbook produced this cycle
        quality = None
        if not skip_compile and os.path.exists(curriculum_dir):
            quality = evaluate(topic, curriculum_dir, cycle=cycle)
            log_report(quality, log)
            wd.emit("quality", "gate",
                    score=quality.overall_score,
                    facts=quality.fact_confidence,
                    pedagogy=quality.pedagogy_coverage,
                    duplicates=quality.duplicate_headings,
                    contradictions=quality.contradictions,
                    usable=quality.is_usable)
        final_quality = quality

        # Stop: textbook is usable
        if quality and quality.is_usable:
            log.info(f"[LOOP] Textbook USABLE — score={quality.overall_score:.2f} — stopping")
            break

        # Stop: hard cycle ceiling
        if cycle >= max_cycles:
            log.info(f"[LOOP] Max cycles ({max_cycles}) reached — stopping")
            break

        # Stop: corpus exhausted
        if cycle > 1 and current_file_count == prev_file_count:
            log.info("[LOOP] No new files harvested — sources exhausted, stopping")
            break

        # Stop: quality plateaued (no meaningful improvement)
        if quality and not has_improved(prev_quality, quality):
            log.info(
                f"[LOOP] Quality plateaued — "
                f"{prev_quality.overall_score:.2f} → {quality.overall_score:.2f}, stopping"
            )
            break

        # Stop: no gap nodes to pursue
        if not gap_nodes:
            log.info("[LOOP] No gap nodes generated — stopping")
            break

        prev_file_count = current_file_count
        prev_quality = quality

        # Inject gap nodes so next cycle's bootstrap picks them up
        _inject_gap_nodes(topic, gap_nodes, log)

        # After cycle 1, when foundational coverage is present, expand the
        # frontier with theoretical / advanced "deep-dive" nodes that target
        # material science, advanced techniques, comparative methods.
        if cycle == 1:
            try:
                from agents.researcher_agent import ResearcherAgent
                dd_researcher = ResearcherAgent(topic)
                dd_researcher.bootstrap()
                dd_researcher.generate_deep_dive_nodes(min_corpus=5)
            except Exception as e:
                log.debug(f"[LOOP] deep-dive expansion error: {e}")

        # Reset fingerprints between cycles. The vault was emptied by
        # cleanup, so persisting fingerprints from cycle N just blocks
        # cycle N+1 from re-attempting borderline content with the new
        # gap-node frontier. Reset gives the loop a real second chance.
        try:
            from config import topic_hash_path
            fp = topic_hash_path(topic)
            if os.path.exists(fp):
                os.remove(fp)
                log.info(f"[LOOP] Reset fingerprints for cycle {cycle + 1}")
        except Exception as e:
            log.debug(f"[LOOP] fingerprint reset error: {e}")

    wd.end_run()
    log.info("DONE")

    vault_path = os.path.join(config.VAULT_ROOT, topic)
    file_count = len(
        [f for f in os.listdir(vault_path) if f.endswith(".txt")]
    ) if os.path.exists(vault_path) else 0

    return {
        "cycles_run": cycles_run,
        "files": file_count,
        "final_quality": final_quality,
        "gap_nodes": final_gap_nodes,
    }


def main(
    topic: str,
    min_files: int = 100,
    max_iterations: int = 5,
    skip_compile: bool = False,
    max_cycles: int = 1,
    hub_urls: list[str] | None = None,
):
    log = _setup_logging(topic)

    try:
        config.init_dirs()
    except Exception as e:
        log.error(f"Failed to create required directories: {e}")
        return

    run_harvest(
        topic,
        min_files=min_files,
        max_iterations=max_iterations,
        max_cycles=max_cycles,
        skip_compile=skip_compile,
        hub_urls=hub_urls,
        log=log,
    )


if __name__ == "__main__":
    raw_topic = sys.argv[1].strip() if len(sys.argv) > 1 else "welding"
    target = re.sub(r'\s+', '_', re.sub(r'[^\w\s-]', '', raw_topic)).strip('_')
    if not target:
        print("Invalid topic name.")
        sys.exit(1)

    try:
        min_files  = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        max_iter   = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        max_cycles = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    except ValueError:
        print("Usage: python main.py <topic> [min_files] [max_iterations] [max_cycles] [--skip-compile]")
        sys.exit(1)

    skip_compile = "--skip-compile" in sys.argv

    hub_urls = []
    for i, arg in enumerate(sys.argv):
        if arg == "--hub" and i + 1 < len(sys.argv):
            hub_urls.append(sys.argv[i + 1])

    main(target, min_files, max_iter, skip_compile, max_cycles, hub_urls or None)
