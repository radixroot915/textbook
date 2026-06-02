"""watchdog — structured pipeline observer.

Each pipeline phase emits events to a singleton watchdog. The watchdog:
  1. Aggregates rolling metrics across the run
  2. Applies rule-based alerts (pollution snowball, stagnation, dead agents)
  3. Writes a structured scorecard + human-readable summary per run
  4. Optionally signals abort conditions back to the pipeline

Usage from a pipeline phase:
    from watchdog import wd
    wd.emit("harvest", "save", source="reddit", size=12000)
    wd.emit("harvest", "gate_reject", gate="drift", reason="off-topic")
    wd.phase_start("compile.chapter_write")
    ...
    wd.phase_end("compile.chapter_write")
    wd.emit("quality", "gate", score=0.74, facts=0.69, usable=False)

Watchdog persists to runs/<topic>/<run_id>/ as scorecard.json + summary.md.
"""

import os
import json
import time
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, UTC

log = logging.getLogger(__name__)


class Watchdog:
    def __init__(self):
        self._lock = threading.Lock()
        self.topic: str = "unknown"
        self.run_id: str = ""
        self.run_start: float = 0.0
        self.events: list = []
        self.metrics: dict = defaultdict(int)
        self.metrics_by_cycle: dict = defaultdict(lambda: defaultdict(int))
        self.metrics_by_agent: dict = defaultdict(lambda: defaultdict(int))
        self.phase_timings: dict = {}
        self.alerts: list = []
        self.cycle: int = 0
        self.recent_rejections: deque = deque(maxlen=20)
        self.cycle_quality: list = []
        self.abort_requested: bool = False
        # filter_effects[cycle][filter_name][stat] -> count
        # Tracks strip/replace activity per filter per cycle so we can
        # spot "filter X stripped 5× more this cycle, score dropped"
        # — exactly the diagnostic that would have caught v10's regression.
        self.filter_effects: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    # -------------------------------------------------------------------
    # Lifecycle

    def start_run(self, topic: str):
        with self._lock:
            self.topic = topic
            self.run_start = time.time()
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            self.run_id = f"{topic}_{ts}"
            self.events = []
            self.metrics = defaultdict(int)
            self.metrics_by_cycle = defaultdict(lambda: defaultdict(int))
            self.metrics_by_agent = defaultdict(lambda: defaultdict(int))
            self.phase_timings = {}
            self.alerts = []
            self.cycle = 0
            self.recent_rejections = deque(maxlen=20)
            self.cycle_quality = []
            self.abort_requested = False
            self.filter_effects = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
            log.info(f"[watchdog] run started: {self.run_id}")

    def set_cycle(self, cycle: int):
        with self._lock:
            self.cycle = cycle

    def end_run(self):
        with self._lock:
            self.metrics["run_duration_sec"] = int(time.time() - self.run_start)
            self._write_scorecard()
            log.info(f"[watchdog] run ended: {self.run_id} "
                     f"({self.metrics['run_duration_sec']}s, "
                     f"{len(self.alerts)} alerts)")

    # -------------------------------------------------------------------
    # Events

    def emit(self, phase: str, event_type: str, **data):
        with self._lock:
            ts = time.time() - self.run_start
            self.events.append({
                "ts": round(ts, 2),
                "cycle": self.cycle,
                "phase": phase,
                "type": event_type,
                **data,
            })

            # Update counters
            self.metrics[f"{phase}.{event_type}"] += 1
            self.metrics_by_cycle[self.cycle][f"{phase}.{event_type}"] += 1

            if "source" in data:
                self.metrics_by_agent[data["source"]][event_type] += 1

            # Apply rules per event type
            if event_type == "gate_reject":
                self.recent_rejections.append(data.get("gate", "unknown"))
                self._check_pollution_snowball()
            elif event_type == "gate" and phase == "quality":
                self.cycle_quality.append({
                    "cycle": self.cycle,
                    "score": data.get("score", 0),
                    "facts": data.get("facts", 0),
                    "pedagogy": data.get("pedagogy", 0),
                })
                self._check_quality_plateau()
                # Quality lands AFTER compile filters, so retroactively
                # check every filter touched this cycle for over-stripping
                for fname in self.filter_effects.get(self.cycle, {}).keys():
                    self._check_filter_regression(fname)
            elif event_type == "save":
                # Reset rejection counter on success
                pass
            elif event_type == "ollama_error":
                self.metrics["ollama_errors"] += 1
                if self.metrics["ollama_errors"] >= 5:
                    self._raise_alert("ollama_unstable",
                                      f"{self.metrics['ollama_errors']} Ollama errors")
            elif event_type == "filter_activity":
                # data fields: name=<filter>, stripped=N, replaced=N, ...
                name = data.get("name", "unknown")
                for key, val in data.items():
                    if key == "name" or not isinstance(val, (int, float)):
                        continue
                    self.filter_effects[self.cycle][name][key] += int(val)
                self._check_filter_regression(name)

    def phase_start(self, phase: str):
        with self._lock:
            self.phase_timings.setdefault(phase, {"calls": 0, "total_sec": 0,
                                                  "start": None})
            self.phase_timings[phase]["start"] = time.time()
            self.phase_timings[phase]["calls"] += 1

    def phase_end(self, phase: str):
        with self._lock:
            t = self.phase_timings.get(phase)
            if not t or t.get("start") is None:
                return
            elapsed = time.time() - t["start"]
            t["total_sec"] += elapsed
            t["start"] = None

    # -------------------------------------------------------------------
    # Alert rules

    def _check_pollution_snowball(self):
        """If recent rejections show drift dominating, alert."""
        if len(self.recent_rejections) < 10:
            return
        drift_count = sum(1 for r in self.recent_rejections if "drift" in r)
        if drift_count >= 7:
            self._raise_alert(
                "pollution_snowball",
                f"{drift_count}/{len(self.recent_rejections)} recent "
                f"rejections were drift — upstream gap-analysis may be polluted",
            )
            # Could also flip abort_requested = True for critical cases

    def _check_filter_regression(self, filter_name: str):
        """If a filter's strip count jumps significantly cycle-over-cycle AND
        quality dropped, alert. This is the diagnostic that would have caught
        v10's hallucination_filter over-stripping."""
        if self.cycle < 2:
            return
        prev = self.filter_effects.get(self.cycle - 1, {}).get(filter_name, {})
        curr = self.filter_effects.get(self.cycle, {}).get(filter_name, {})
        if not prev or not curr:
            return
        # Look at the dominant counter (max value) in current cycle
        if not curr:
            return
        dominant_key = max(curr.keys(), key=lambda k: curr[k])
        curr_v = curr.get(dominant_key, 0)
        prev_v = prev.get(dominant_key, 0)
        if prev_v == 0 or curr_v < 5:
            return
        ratio = curr_v / prev_v
        # Cross-reference with quality drop
        if ratio < 2.0:
            return
        quality_dropped = False
        if len(self.cycle_quality) >= 2:
            quality_dropped = (
                self.cycle_quality[-1]["score"]
                < self.cycle_quality[-2]["score"] - 0.03
            )
        if quality_dropped:
            self._raise_alert(
                "filter_over_stripping",
                f"{filter_name}.{dominant_key} jumped {prev_v} → {curr_v} "
                f"({ratio:.1f}×) while score dropped",
            )

    def _check_quality_plateau(self):
        """If quality score barely moves cycle-over-cycle, alert."""
        if len(self.cycle_quality) < 2:
            return
        prev = self.cycle_quality[-2]["score"]
        curr = self.cycle_quality[-1]["score"]
        if curr < prev - 0.05:
            self._raise_alert(
                "quality_regression",
                f"Score dropped {prev:.2f} → {curr:.2f} between cycles",
            )
        elif abs(curr - prev) < 0.02:
            self._raise_alert(
                "quality_plateau",
                f"Score barely moved ({prev:.2f} → {curr:.2f}) — may be stuck",
            )

    def _raise_alert(self, kind: str, message: str):
        alert = {"cycle": self.cycle, "kind": kind, "message": message,
                 "ts": round(time.time() - self.run_start, 2)}
        # Dedup repeated same-kind alerts in same cycle
        for a in self.alerts:
            if a["cycle"] == self.cycle and a["kind"] == kind:
                return
        self.alerts.append(alert)
        log.warning(f"[watchdog] ALERT {kind}: {message}")

    # -------------------------------------------------------------------
    # Reports

    def _write_scorecard(self):
        try:
            from config import BASE_DIR
            out_dir = os.path.join(BASE_DIR, "runs", self.run_id)
            os.makedirs(out_dir, exist_ok=True)

            # Compute agent productivity
            agent_summary = {}
            for agent, m in self.metrics_by_agent.items():
                saves = m.get("save", 0)
                rejects = m.get("gate_reject", 0)
                tries = saves + rejects
                if tries > 0:
                    agent_summary[agent] = {
                        "saves": saves, "rejects": rejects,
                        "save_rate": round(saves / tries, 3),
                    }

            # Phase timing summary
            phase_summary = {}
            for phase, t in self.phase_timings.items():
                phase_summary[phase] = {
                    "calls": t["calls"],
                    "total_sec": round(t["total_sec"], 1),
                    "avg_sec": round(t["total_sec"] / max(t["calls"], 1), 2),
                }

            # Normalise filter_effects for JSON
            filter_effects_out = {
                str(cycle): {fname: dict(stats) for fname, stats in by_filter.items()}
                for cycle, by_filter in self.filter_effects.items()
            }

            scorecard = {
                "run_id": self.run_id,
                "topic": self.topic,
                "duration_sec": self.metrics["run_duration_sec"],
                "alerts": self.alerts,
                "cycle_quality": self.cycle_quality,
                "metrics": dict(self.metrics),
                "metrics_by_cycle": {k: dict(v) for k, v in self.metrics_by_cycle.items()},
                "agent_productivity": agent_summary,
                "phase_timings": phase_summary,
                "filter_effects": filter_effects_out,
                "event_count": len(self.events),
            }

            with open(os.path.join(out_dir, "scorecard.json"), "w", encoding="utf-8") as f:
                json.dump(scorecard, f, indent=2, default=str)

            # Human-readable summary
            self._write_summary_md(out_dir, scorecard)

            # Full event log (large but useful for forensics)
            with open(os.path.join(out_dir, "timeline.jsonl"), "w", encoding="utf-8") as f:
                for e in self.events:
                    f.write(json.dumps(e) + "\n")

            log.info(f"[watchdog] scorecard → {out_dir}")
        except Exception as e:
            log.warning(f"[watchdog] scorecard write error: {e}")

    def _write_summary_md(self, out_dir: str, sc: dict):
        lines = [
            f"# Run Summary — {sc['topic']}",
            "",
            f"**Run ID:** `{sc['run_id']}`",
            f"**Duration:** {sc['duration_sec']}s",
            f"**Events:** {sc['event_count']}",
            "",
            "## Cycle Quality",
            "",
            "| Cycle | Score | Facts | Pedagogy |",
            "|-------|-------|-------|----------|",
        ]
        for q in sc["cycle_quality"]:
            lines.append(
                f"| {q['cycle']} | {q['score']:.2f} | "
                f"{q['facts']:.0%} | {q['pedagogy']:.0%} |"
            )

        if sc["alerts"]:
            lines += ["", "## Alerts", ""]
            for a in sc["alerts"]:
                lines.append(f"- **{a['kind']}** (cycle {a['cycle']}): {a['message']}")
        else:
            lines += ["", "## Alerts", "", "*No alerts raised.*"]

        if sc["agent_productivity"]:
            lines += ["", "## Agent Productivity", "",
                      "| Agent | Saves | Rejects | Save Rate |",
                      "|-------|-------|---------|-----------|"]
            sorted_agents = sorted(sc["agent_productivity"].items(),
                                   key=lambda x: -x[1]["save_rate"])
            for agent, m in sorted_agents:
                lines.append(f"| {agent} | {m['saves']} | {m['rejects']} | "
                             f"{m['save_rate']:.0%} |")

        if sc["phase_timings"]:
            lines += ["", "## Phase Timings (top 10 by total time)", "",
                      "| Phase | Calls | Total (s) | Avg (s) |",
                      "|-------|-------|-----------|---------|"]
            sorted_phases = sorted(sc["phase_timings"].items(),
                                   key=lambda x: -x[1]["total_sec"])[:10]
            for phase, t in sorted_phases:
                lines.append(f"| {phase} | {t['calls']} | "
                             f"{t['total_sec']} | {t['avg_sec']} |")

        # Filter-effects trend: per-filter, per-cycle activity counts
        fe = sc.get("filter_effects") or {}
        if fe:
            cycles_sorted = sorted(fe.keys(), key=lambda c: int(c))
            # Collect (filter, stat) pairs that appear in any cycle
            seen: dict = {}
            for c in cycles_sorted:
                for fname, stats in fe[c].items():
                    for stat_key in stats:
                        seen.setdefault((fname, stat_key), True)
            if seen:
                header = "| Filter.Stat | " + " | ".join(f"C{c}" for c in cycles_sorted) + " | Trend |"
                divider = "|" + "---|" * (len(cycles_sorted) + 2)
                lines += ["", "## Filter Effects (cycle-over-cycle)", "", header, divider]
                for fname, stat_key in sorted(seen.keys()):
                    row_vals = [fe[c].get(fname, {}).get(stat_key, 0) for c in cycles_sorted]
                    if all(v == 0 for v in row_vals):
                        continue
                    if len(row_vals) >= 2 and row_vals[-2] > 0:
                        ratio = row_vals[-1] / row_vals[-2]
                        if ratio >= 2.0:
                            trend = f"+{ratio:.1f}x"
                        elif ratio <= 0.5:
                            trend = f"-{1/ratio:.1f}x"
                        else:
                            trend = "≈"
                    else:
                        trend = "—"
                    row = "| `" + f"{fname}.{stat_key}" + "` | " + " | ".join(str(v) for v in row_vals) + f" | {trend} |"
                    lines.append(row)

        with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


# Singleton instance
wd = Watchdog()
