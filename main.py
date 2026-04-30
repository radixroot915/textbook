import os
import re
import sys
import logging
import asyncio
import traceback
import config
from llm.ollama_client import health_check
from agents.coordinator import Coordinator


def main(topic: str, min_files: int = 100, max_iterations: int = 5):
    log_path = os.path.join(config.BASE_DIR, f"{topic}_run.log")
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(message)s",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(sys.stdout)
            ]
        )
    else:
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_path)
                   for h in root.handlers):
            root.addHandler(logging.FileHandler(log_path, encoding="utf-8"))
    log = logging.getLogger("harvester")

    try:
        config.init_dirs()
    except Exception as e:
        log.error(f"Failed to create required directories: {e}")
        return

    log.info(f"START topic={topic} min_files={min_files} max_iter={max_iterations}")

    if not health_check():
        log.error("Ollama not accessible — exiting.")
        return

    log.info("Ollama OK — creating coordinator")
    coordinator = Coordinator(topic, min_files, max_iterations)
    try:
        asyncio.run(coordinator.run())
        log.info("DONE")
    except Exception as e:
        log.error(f"Coordinator crashed: {e}")
        log.error(traceback.format_exc())


if __name__ == "__main__":
    raw_topic = sys.argv[1].strip() if len(sys.argv) > 1 else "welding"
    target = re.sub(r'\s+', '_', re.sub(r'[^\w\s-]', '', raw_topic)).strip('_')
    if not target:
        print("Invalid topic name.")
        sys.exit(1)
    try:
        min_files = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        max_iter = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    except ValueError:
        print("Usage: python main.py <topic> [min_files] [max_iterations]")
        sys.exit(1)
    main(target, min_files, max_iter)
