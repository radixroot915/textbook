"""
Harvester workflow CLI
----------------------
Commands:
  status                       Show build state of all topics in vault
  quality  <topic>             Show quality scores, claim DB stats, and drift for a topic
  verify   <topic> [--sample N]  Random-sample claim DB and emit verification markdown
  repair   <topic>             Fill missing classifications/claims for partial vault files
  smoke    <topic> [--min-files N]  Comprehensive single-cycle test (~15-20 min, exits 0/1/2)
  compile  <topic>             Compile curriculum from existing vault (no new harvest)
  export   <topic>             Export PDF from existing textbook
  run      <topic>             compile + export (no new harvest)
  harvest  <topic> [options]   Full harvest + compile + export

Examples:
  python workflow.py status
  python workflow.py quality leatherworking
  python workflow.py compile carpentry
  python workflow.py export carpentry
  python workflow.py run carpentry
  python workflow.py harvest "stair construction" --min-files 80 --iterations 5 --cycles 2
"""

import os
import sys
import json
import time
import asyncio
import logging
import argparse
import re
from pathlib import Path

# Ensure harvester root is on the path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import config

# ---------------------------------------------------------------------------
# Terminal colours (no external deps)

def _c(code): return f"\033[{code}m"
RESET, BOLD, DIM = _c(0), _c(1), _c(2)
GREEN, YELLOW, RED, CYAN = _c(32), _c(33), _c(31), _c(36)

def ok(s):   return f"{GREEN}{BOLD}{s}{RESET}"
def warn(s): return f"{YELLOW}{s}{RESET}"
def err(s):  return f"{RED}{s}{RESET}"
def hdr(s):  return f"{CYAN}{BOLD}{s}{RESET}"
def dim(s):  return f"{DIM}{s}{RESET}"

# ---------------------------------------------------------------------------
# Helpers

def _slug(topic: str) -> str:
    return re.sub(r'\s+', '_', re.sub(r'[^\w\s-]', '', topic)).strip('_')


def _topic_state(slug: str) -> dict:
    base = Path(config.VAULT_ROOT) / slug
    cur  = base / "curriculum"
    return {
        "slug":      slug,
        "vault":     base.exists(),
        "files":     len(list(base.glob("*.txt"))) if base.exists() else 0,
        "grit":      (cur / f"{slug}_grit.json").exists(),
        "textbook":  (cur / f"{slug}_textbook.md").exists(),
        "pdf":       (cur / f"{slug}_textbook.pdf").exists(),
        "plan":      (cur / f"{slug}_curriculum.json").exists(),
        "materials": (cur / f"{slug}_materials.json").exists(),
        "videos":    (cur / f"{slug}_videos.json").exists(),
    }


def _setup_log(slug: str) -> logging.Logger:
    log_path = os.path.join(ROOT, f"{slug}_run.log")
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
    return logging.getLogger("harvester")


def _load_grit(slug: str) -> list:
    grit_path = Path(config.VAULT_ROOT) / slug / "curriculum" / f"{slug}_grit.json"
    if not grit_path.exists():
        return []
    with open(grit_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("grit", data) if isinstance(data, dict) else data


def _load_lexicon(slug: str) -> list:
    try:
        with open(config.MAP_PATH, encoding="utf-8") as f:
            km = json.load(f)
        return km.get(slug, {}).get("lexicon", [])
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Commands

def _stage(s: dict) -> tuple[str, str]:
    """Return (label, suggested next command) based on build state."""
    slug = s["slug"]
    if s["files"] == 0:
        return warn("empty vault"), f"python workflow.py harvest {slug}"
    if s["files"] < 10:
        return warn(f"thin vault ({s['files']} files)"), f"python workflow.py harvest {slug} --min-files 80"
    if not s["grit"] and not s["textbook"]:
        return warn("needs compile"), f"python workflow.py compile {slug}"
    if s["textbook"] and not s["pdf"]:
        return warn("needs PDF export"), f"python workflow.py export {slug}"
    if s["textbook"] and s["pdf"] and s["plan"] and s["materials"] and s["videos"]:
        return ok("complete"), ""
    return warn("partial compile"), f"python workflow.py run {slug}"


def _bar(s: dict) -> str:
    """5-segment pipeline bar: [vault][grit][text][pdf][plan+mat+vid]"""
    def seg(filled, char="="):
        return ok(f"[{char}]") if filled else dim("[ ]")
    all_out = s["plan"] and s["materials"] and s["videos"]
    return (
        seg(s["files"] > 0, "V") +
        seg(s["grit"],      "G") +
        seg(s["textbook"],  "T") +
        seg(s["pdf"],       "P") +
        seg(all_out,        "O")
    )


def cmd_status(args):
    topics = sorted(os.listdir(config.VAULT_ROOT)) if os.path.exists(config.VAULT_ROOT) else []
    if not topics:
        print(warn("Vault is empty — run: python workflow.py harvest <topic>"))
        return

    W = 22  # topic col width

    print()
    print(f"  {hdr('TOPIC'.ljust(W))}  {'FILES':>5}  {'PIPELINE':^17}  STATUS")
    print(f"  {dim('-' * W)}  {dim('-----')}  {dim('V  G  T  P  O ')}  {dim('-' * 30)}")

    next_cmds = []
    for t in topics:
        s           = _topic_state(t)
        label, cmd  = _stage(s)
        bar         = _bar(s)
        name        = s["slug"].replace("_", " ")
        print(f"  {name.ljust(W)}  {str(s['files']).rjust(5)}  {bar}  {label}")
        if cmd:
            next_cmds.append((s["slug"], cmd))

    # Pipeline key
    print()
    print(f"  {dim('Pipeline: [V]ault  [G]rit  [T]extbook  [P]DF  [O]utputs (plan+materials+videos)')}")

    # Suggested next steps
    if next_cmds:
        print()
        print(f"  {hdr('Suggested next steps:')}")
        for slug, cmd in next_cmds:
            print(f"    {dim(slug.replace('_',' ').ljust(W))}  {CYAN}{cmd}{RESET}")
    print()


def cmd_quality(args):
    """Compose scorecards + claim DB stats + drift summary into one snapshot."""
    slug = _slug(args.topic)
    runs_dir = Path(ROOT) / "runs"

    print(hdr(f"\n=== Quality overview: {slug} ===\n"))

    # 1. Scorecards across runs
    cards = sorted(runs_dir.glob(f"{slug}_*/scorecard.json")) if runs_dir.exists() else []
    if not cards:
        print(dim("  No quality runs recorded yet.\n"))
    else:
        print(f"  {hdr('Runs:')}")
        for card_path in cards:
            try:
                with open(card_path, encoding="utf-8") as f:
                    card = json.load(f)
            except Exception:
                continue
            run_id = card.get("run_id", card_path.parent.name)
            dur = int(card.get("duration_sec", 0))
            print(f"    {dim(run_id)}  ({dur // 60}m{dur % 60}s)")
            cycle_q = card.get("cycle_quality", [])
            if not cycle_q:
                print(dim("      (no cycle quality recorded)"))
            for cq in cycle_q:
                c     = cq.get("cycle", "?")
                score = cq.get("score", 0) or 0
                facts = cq.get("facts", 0) or 0
                ped   = cq.get("pedagogy", 0) or 0
                if score >= 0.74:
                    tag = ok("USABLE")
                elif score >= 0.70:
                    tag = ""
                else:
                    tag = warn("below floor")
                print(f"      cycle {c}: score={score:.2f} facts={facts*100:.0f}% pedagogy={ped*100:.0f}%  {tag}")
            for a in card.get("alerts", []):
                kind = a.get("kind", "?")
                msg  = a.get("message", "?")
                print(f"      {warn('alert:')} {kind} — {msg}")
        print()

    # 2. Claims DB
    try:
        from claims_db import db_stats
        stats = db_stats(slug)
    except Exception:
        stats = {"total": 0}
    total = stats.get("total", 0)
    if total:
        print(f"  {hdr('Claims:')}")
        print(f"    total: {total}")
        by_src = stats.get("by_source", {})
        if by_src:
            srcs = ", ".join(f"{k}={v}" for k, v in sorted(by_src.items(), key=lambda x: -x[1]))
            print(f"    sources: {srcs}")
        by_type = stats.get("by_type", {})
        if by_type:
            types = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1]))
            print(f"    types: {types}")
        lt = stats.get("low_trust", 0)
        if lt:
            pct = (lt * 100) // total
            print(f"    low-trust: {lt} ({pct}%)")
        print()
    else:
        print(dim("  Claims: (none extracted yet)\n"))

    # 3. Drift summary
    try:
        from drift_monitor import drift_summary
        drift = drift_summary(slug, window=20)
    except Exception:
        drift = {"recent_entries": 0}
    n = drift.get("recent_entries", 0)
    if n:
        print(f"  {hdr(f'Drift (last {n} saves):')}")
        print(f"    reference_ratio: {drift.get('reference_ratio', 0):.2f}")
        print(f"    narrative_ratio: {drift.get('narrative_ratio', 0):.2f}")
        print(f"    avg_topic_density: {drift.get('avg_topic_density', 0):.2f}")
        print()
    else:
        print(dim("  Drift: (no save log yet)\n"))

    # 4. Dashboard benchmarks — three numbers that surface healthy vs investigate
    try:
        from core.benchmarks import run_health
        from claims_db import _load as _load_claims
        claims_db = _load_claims(slug)
        # Use the most recent scorecard for the run-level benchmarks
        if cards:
            latest_card_path = cards[-1]
            try:
                with open(latest_card_path, encoding="utf-8") as f:
                    latest_card = json.load(f)
            except Exception:
                latest_card = {}
        else:
            latest_card = {}
        health = run_health(claims_db, latest_card)
        print(f"  {hdr('Benchmarks (latest run):')}")
        ref_share = health["reference_source_share"]
        ref_label = ok("healthy") if ref_share >= 0.40 else (warn("low") if ref_share >= 0.20 else err("critical"))
        print(f"    reference_source_share: {ref_share:.0%}  {ref_label}")
        wd = health["worst_cycle_delta"]
        if wd is None:
            print(f"    worst_cycle_delta:      n/a (single-cycle run)")
        else:
            wd_label = ok("ok") if wd >= 0 else (warn("regression") if wd > -0.05 else err("regression"))
            print(f"    worst_cycle_delta:      {wd:+.2f}  {wd_label}")
        alerts = health.get("filter_ratio_alerts", [])
        if alerts:
            print(f"    {warn('filter alerts:')}")
            for a in alerts[:3]:
                print(f"      {a['filter']}.{a['stat']}: {a['prev']} → {a['curr']} ({a['ratio']:.1f}×)")
        else:
            print(f"    filter_ratio_alerts:    {ok('none')}")
        if health["verdict"] == "investigate":
            print(f"    {warn('verdict:')} {warn('investigate')}")
        else:
            print(f"    {ok('verdict:')} {ok('healthy')}")
        print()
    except Exception as e:
        print(dim(f"  Benchmarks: error ({e})\n"))


def cmd_probe(args):
    """Run a probe — small isolated verification of one system behavior.
    Use for de-risking architectural pivots before full-pipeline tests."""
    from core import probes

    name = args.probe_name
    if name == "list":
        reg = probes.registry()
        print(hdr(f"\n=== Available probes ===\n"))
        for n, info in sorted(reg.items()):
            print(f"  {ok(n.ljust(28))} {dim(info['description'])}")
        print()
        return

    info = probes.get(name)
    if not info:
        print(err(f"Unknown probe: {name}"))
        print(dim(f"  Run 'workflow.py probe list' to see available probes."))
        sys.exit(2)

    print(hdr(f"\n=== Probe: {name} ===\n"))
    print(dim(f"  {info['description']}\n"))

    started = time.time()
    try:
        result = info["fn"](args)
    except Exception as e:
        print(err(f"Probe crashed: {e}"))
        sys.exit(2)
    elapsed = int(time.time() - started)

    # Render result
    print(f"  {hdr('Summary:')}    {result.summary}")
    if result.metrics:
        print(f"  {hdr('Metrics:')}")
        for k, v in result.metrics.items():
            print(f"    {k}: {v}")
    if result.notes and args.show_notes:
        print(f"  {hdr('Notes:')}")
        for n in result.notes:
            print(n)

    # Banner verdict
    print()
    if result.verdict == "PASS":
        print(f"{ok('==========  PROBE: PASS  ' + '=' * 30)}")
        sys.exit(0)
    elif result.verdict == "INCONCLUSIVE":
        print(f"{warn('======  PROBE: INCONCLUSIVE  ' + '=' * 26)}")
        sys.exit(2)
    else:
        print(f"{err('==========  PROBE: FAIL  ' + '=' * 30)}")
        sys.exit(1)


def cmd_smoke(args):
    """Comprehensive but fast integration test.

    Single-cycle, small-corpus harvest → compile → fact-check → filters →
    quality gate. Targets ~15-20 min so iteration on changes doesn't require
    a 55-min full run. Ends with benchmark dashboard + exit code 0 if the
    run passes the health verdict.
    """
    slug = _slug(args.topic)
    log  = _setup_log(slug)

    print(hdr(f"\n=== SMOKE: {slug} | min={args.min_files} iter=1 cycles=1 ===\n"))
    print(dim("  Comprehensive single-cycle test. Exits 0 on healthy verdict, 1 on investigate.\n"))

    try:
        config.init_dirs()
    except Exception as e:
        print(err(f"Failed to init dirs: {e}"))
        sys.exit(2)

    # Wipe transient state so the smoke test runs against a clean slate
    # (claims DB preserved — it compounds across runs by design).
    for p in [
        Path(ROOT) / f"fingerprints_{slug}.txt",
        Path(ROOT) / "cited_urls.json",
        Path(ROOT) / f"drift_log_{slug}.json",
    ]:
        if p.exists():
            p.unlink()
    vault = Path(config.VAULT_ROOT) / slug
    if vault.exists():
        for f in vault.glob("*.txt"):
            f.unlink()

    # Smoke-mode env knobs — cap chapter count + skip image fetching so the
    # pipeline runs proportional load. Halves compile time without changing
    # any of the diagnostic surface (claims, filters, fact-check, quality gate).
    os.environ.setdefault("HARVESTER_MAX_CHAPTERS", str(args.max_chapters))
    os.environ.setdefault("HARVESTER_SKIP_IMAGES", "1")

    import time
    started = time.time()

    from main import run_harvest
    result = run_harvest(
        slug,
        min_files=args.min_files,
        max_iterations=1,
        max_cycles=1,
        skip_compile=False,
        log=log,
    )

    elapsed = int(time.time() - started)
    print(hdr(f"\n=== SMOKE COMPLETE in {elapsed // 60}m{elapsed % 60}s ===\n"))

    # Run benchmark dashboard
    print(f"  Cycles run : {result['cycles_run']}")
    print(f"  Files      : {result['files']}")
    print(f"  Gap nodes  : {len(result.get('gap_nodes', []))}")
    fq = result.get("final_quality")
    if fq is not None:
        verdict_label = ok("USABLE") if fq.is_usable else warn(f"score={fq.overall_score:.2f}")
        print(f"  Quality    : {verdict_label} (facts={fq.fact_confidence:.0%} pedagogy={fq.pedagogy_coverage:.0%})")
    print()

    # Apply the dashboard benchmarks to the just-written scorecard
    try:
        from core.benchmarks import run_health
        from claims_db import _load as _load_claims
        runs_dir = Path(ROOT) / "runs"
        cards = sorted(runs_dir.glob(f"{slug}_*/scorecard.json"))
        latest = {}
        if cards:
            with open(cards[-1], encoding="utf-8") as f:
                latest = json.load(f)
        claims_db = _load_claims(slug)
        health = run_health(claims_db, latest)

        print(f"  {hdr('Benchmarks:')}")
        ref_share = health["reference_source_share"]
        ref_label = ok("healthy") if ref_share >= 0.40 else (warn("low") if ref_share >= 0.20 else err("critical"))
        print(f"    reference_source_share: {ref_share:.0%}  {ref_label}")
        delta = health["worst_cycle_delta"]
        if delta is None:
            print(f"    worst_cycle_delta:      n/a (single-cycle)")
        else:
            d_label = ok("ok") if delta >= 0 else (warn("regression") if delta > -0.05 else err("regression"))
            print(f"    worst_cycle_delta:      {delta:+.2f}  {d_label}")
        alerts = health.get("filter_ratio_alerts", [])
        if alerts:
            print(f"    {warn('filter alerts:')}")
            for a in alerts[:3]:
                print(f"      {a['filter']}.{a['stat']}: {a['prev']} → {a['curr']} ({a['ratio']:.1f}×)")
        else:
            print(f"    filter_ratio_alerts:    {ok('none')}")

        print()
        if health["verdict"] == "healthy":
            banner = ok("==================  SMOKE TEST: HEALTHY  ==================")
            print(f"\n{banner}\n")
            sys.exit(0)
        else:
            banner = warn("==============  SMOKE TEST: INVESTIGATE  ==============")
            print(f"\n{banner}")
            for issue in health["issues"][:5]:
                print(f"  {warn('-')} {issue}")
            print(f"{warn('=' * 56)}\n")
            sys.exit(1)
    except Exception as e:
        banner = err("==================  SMOKE TEST: ERROR  ==================")
        print(f"\n{banner}\n  {e}\n{err('=' * 56)}\n")
        sys.exit(2)


def cmd_repair(args):
    """Recovery pass — fill missing classifications/claims for vault files
    whose processing was interrupted (e.g. by an Ollama outage)."""
    slug = _slug(args.topic)
    vault_path = Path(config.VAULT_ROOT) / slug

    if not vault_path.exists():
        print(err(f"No vault for '{slug}'. Run harvest first."))
        sys.exit(1)

    files = sorted(vault_path.glob("*.txt"))
    if not files:
        print(err(f"Vault for '{slug}' is empty."))
        sys.exit(1)

    print(hdr(f"\n=== Repair: {slug} | {len(files)} vault files ===\n"))

    from classifier import get_classification, classify_file
    from claims_db import _load as _load_claims, extract_and_store_claims
    from agent_stats import _load as _load_stats, _origins_path

    origins = _load_stats(_origins_path()).get(slug, {})
    claims_db = _load_claims(slug)
    files_with_claims = {c.get("source_file") for c in claims_db.get("claims", [])}

    missing_class = []
    missing_claims = []
    for fpath in files:
        fname = fpath.name
        if not get_classification(fname):
            missing_class.append(fpath)
        if fname not in files_with_claims:
            missing_claims.append(fpath)

    print(f"  Missing classification: {len(missing_class)}")
    print(f"  Missing claims:         {len(missing_claims)}")
    if not missing_class and not missing_claims:
        print(ok("\n  Vault is fully indexed — nothing to repair.\n"))
        return

    if missing_class:
        print(f"\n  {hdr('Classifying...')}")
        for fpath in missing_class:
            try:
                with open(fpath, encoding="utf-8") as f:
                    text = f.read()
            except Exception as e:
                print(f"    {warn('skip')} {fpath.name}: {e}")
                continue
            result = classify_file(fpath.name, text, slug)
            if result:
                print(f"    {ok('OK')} {fpath.name[:60]}")
            else:
                print(f"    {warn('--')} {fpath.name[:60]} (classifier returned None)")

    if missing_claims:
        print(f"\n  {hdr('Extracting claims...')}")
        for fpath in missing_claims:
            try:
                with open(fpath, encoding="utf-8") as f:
                    text = f.read()
            except Exception as e:
                print(f"    {warn('skip')} {fpath.name}: {e}")
                continue
            source_name = origins.get(fpath.name, "recovered")
            added = extract_and_store_claims(slug, fpath.name, source_name, text)
            if added > 0:
                print(f"    {ok('OK')} {fpath.name[:60]} (+{added})")
            else:
                print(f"    {warn('--')} {fpath.name[:60]} (no claims extracted)")

    print(hdr(f"\n=== Repair complete ===\n"))
    print(f"  {hdr('Suggested:')} {CYAN}python workflow.py quality {slug}{RESET}\n")


def cmd_verify(args):
    """Random-sample claims from the DB and emit a markdown review file."""
    import random
    slug = _slug(args.topic)

    try:
        from claims_db import _load
        db = _load(slug)
    except Exception as e:
        print(err(f"Could not load claims DB: {e}"))
        sys.exit(1)

    claims = db.get("claims", [])
    if not claims:
        print(err(f"No claims in DB for '{slug}'. Run harvest first."))
        sys.exit(1)

    n = min(args.sample, len(claims))
    sampled = random.sample(claims, n)

    out_path = Path(ROOT) / f"verify_{slug}.md"
    lines = [
        f"# Claim verification — {slug}",
        "",
        f"Random sample of **{n}** claims from **{len(claims)}** total.",
        "For each: tick one box, optional note. Run again to draw a fresh sample.",
        "",
        "---",
        "",
    ]
    for i, c in enumerate(sampled, 1):
        lines.append(f"## {i}. {c.get('text', '')}")
        lines.append("")
        lines.append(f"- **Source:** `{c.get('source_file', '?')}` ({c.get('source_name', '?')})")
        if c.get("low_trust"):
            lines.append(f"- **Trust:** low-trust source")
        nums = c.get("numeric") or []
        if nums:
            lines.append(f"- **Numeric:** {', '.join(str(x) for x in nums)}")
        kws = c.get("keywords") or []
        if kws:
            lines.append(f"- **Keywords:** {', '.join(str(x) for x in kws)}")
        lines.append(f"- **Type:** {c.get('type', '?')}")
        lines.append("")
        lines.append("- [ ] Correct  [ ] Incorrect  [ ] Unclear")
        lines.append("")
        lines.append("Notes:")
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(hdr(f"\n=== Verify sample written ==="))
    print(f"  Sampled {n} of {len(claims)} claims")
    print(f"\n  {ok('Open:')} {out_path}\n")


def cmd_compile(args):
    slug = _slug(args.topic)
    log  = _setup_log(slug)
    s    = _topic_state(slug)

    if not s["vault"] or s["files"] == 0:
        print(err(f"No vault files for '{slug}'. Run harvest first."))
        sys.exit(1)

    print(hdr(f"\n=== Compiling: {slug} ({s['files']} files) ===\n"))

    # Extract grit if not already done
    if not s["grit"]:
        print(warn("No grit found — extracting from vault..."))
        from agents.researcher_agent import ResearcherAgent
        researcher = ResearcherAgent(slug)
        researcher.bootstrap()
        grit = researcher.synthesize_grit()
        log.info(f"Extracted {len(grit)} grit items")
        out_dir = Path(config.VAULT_ROOT) / slug / "curriculum"
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / f"{slug}_grit.json", "w", encoding="utf-8") as f:
            json.dump({"topic": slug, "grit": grit}, f, indent=2)
    else:
        grit = _load_grit(slug)
        print(f"Loaded {len(grit)} grit items from existing file.")

    lexicon = _load_lexicon(slug)
    print(f"Lexicon: {len(lexicon)} terms\n")

    from curriculum.builder import build_curriculum
    from curriculum.video_finder import build_video_guide
    from curriculum.materials import build_materials_list

    result = build_curriculum(slug, lexicon, grit)
    build_video_guide(slug, grit)
    build_materials_list(slug, grit)

    print(hdr(f"\n=== Compile complete ==="))
    tb = result.get("textbook", "—")
    print(f"  Textbook  : {tb}")
    print(f"  Plan      : {result.get('plan', '—')}")
    print(f"  Gap nodes : {len(result.get('gap_nodes', []))}")
    if tb and tb != "—":
        print(f"\n  {ok('Open:')} {tb}")
    print(f"  {hdr('Suggested:')} {CYAN}python workflow.py export {slug}{RESET}")
    print(f"  {hdr('Suggested:')} {CYAN}python workflow.py quality {slug}{RESET}")


def cmd_export(args):
    slug = _slug(args.topic)
    s    = _topic_state(slug)

    tb_path = Path(config.VAULT_ROOT) / slug / "curriculum" / f"{slug}_textbook.md"
    if not tb_path.exists():
        print(err(f"No textbook found for '{slug}'. Run compile first."))
        sys.exit(1)

    print(hdr(f"\n=== Exporting PDF: {slug} ===\n"))
    from curriculum.export_pdf import export_pdf
    result = export_pdf(str(tb_path))
    if result:
        print(ok(f"PDF written: {result}"))
        print(f"\n  {ok('Open:')} {result}")
        print(f"  {hdr('Suggested:')} {CYAN}python workflow.py quality {slug}{RESET}")
    else:
        print(err("PDF export failed — check that fpdf2, weasyprint, or reportlab is installed."))
        sys.exit(1)


def cmd_run(args):
    """Compile + export without harvesting."""
    cmd_compile(args)
    cmd_export(args)


def cmd_expand(args):
    """Add to an existing vault without touching what's already there."""
    s = _topic_state(_slug(args.topic))
    before = s["files"]
    if before == 0:
        print(warn(f"No existing vault for '{args.topic}' — use harvest instead."))
        sys.exit(1)
    print(hdr(f"\n=== Expanding: {args.topic} | existing={before} files ==="))
    print(dim(f"  Dedup scoped to fingerprints_{_slug(args.topic)}.txt — other vaults won't block this run.\n"))
    cmd_harvest(args)
    after = _topic_state(_slug(args.topic))["files"]
    print(f"\n  {ok(f'+{after - before} new files')}  ({before} -> {after} total)")


def cmd_harvest(args):
    slug = _slug(args.topic)
    log  = _setup_log(slug)

    print(hdr(f"\n=== Harvest: {slug} | min={args.min_files} iter={args.iterations} cycles={args.cycles} ===\n"))

    try:
        config.init_dirs()
    except Exception as e:
        print(err(f"Failed to init dirs: {e}"))
        sys.exit(1)

    # Delegate to the unified harvest loop (quality gate + watchdog +
    # fingerprint reset + deep-dive expansion + plateau detection all live
    # there now, so workflow gets parity with main.py).
    from main import run_harvest
    result = run_harvest(
        slug,
        min_files=args.min_files,
        max_iterations=args.iterations,
        max_cycles=args.cycles,
        skip_compile=args.skip_compile,
        log=log,
    )

    print(hdr("\n=== Done ==="))
    print(f"  Cycles run : {result['cycles_run']}")
    print(f"  Files      : {result['files']}")
    print(f"  Gap nodes  : {len(result.get('gap_nodes', []))}")
    fq = result.get("final_quality")
    if fq is not None:
        verdict = ok("USABLE") if fq.is_usable else warn(f"score={fq.overall_score:.2f}")
        print(f"  Quality    : {verdict}")
    print(f"  Log : {ROOT}\\{slug}_run.log")
    print(f"  Out : {config.VAULT_ROOT}\\{slug}\\curriculum")
    pdf_path = Path(config.VAULT_ROOT) / slug / "curriculum" / f"{slug}_textbook.pdf"
    if pdf_path.exists():
        print(f"\n  {ok('Open:')} {pdf_path}")
    print(f"  {hdr('Suggested:')} {CYAN}python workflow.py quality {slug}{RESET}\n")

# ---------------------------------------------------------------------------
# Entry point

def main():
    parser = argparse.ArgumentParser(
        prog="workflow",
        description="Harvester workflow CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Show build state of all vault topics")

    # quality
    p_quality = sub.add_parser("quality", help="Show quality scores, claim DB stats, and drift for a topic")
    p_quality.add_argument("topic")

    # verify
    p_verify = sub.add_parser("verify", help="Random-sample claim DB and emit verification markdown")
    p_verify.add_argument("topic")
    p_verify.add_argument("--sample", type=int, default=40, help="Number of claims to sample (default 40)")

    # repair
    p_repair = sub.add_parser("repair", help="Fill missing classifications/claims for partially-processed vault files")
    p_repair.add_argument("topic")

    # smoke — comprehensive fast integration test
    p_smoke = sub.add_parser("smoke", help="Comprehensive single-cycle pipeline test with benchmark verdict (~20-25 min)")
    p_smoke.add_argument("topic")
    p_smoke.add_argument("--min-files", type=int, default=5, dest="min_files",
                          help="Files to harvest (default 5)")
    p_smoke.add_argument("--max-chapters", type=int, default=4, dest="max_chapters",
                          help="Cap chapters for compile speed (default 4)")

    # probe — small isolated verification (~30s-2min)
    p_probe = sub.add_parser("probe", help="Run a small isolated probe to de-risk a pivot (~30s-2min)")
    p_probe.add_argument("probe_name", help="Probe to run, or 'list'")
    p_probe.add_argument("--topic", default="leatherworking",
                          help="Topic for probes that need one (default leatherworking)")
    p_probe.add_argument("--claims", type=int, default=40,
                          help="Claim count for claim-attribution probe (default 40)")
    p_probe.add_argument("--show-notes", action="store_true",
                          help="Print probe notes (e.g. generated LLM output)")

    # compile
    p_compile = sub.add_parser("compile", help="Compile curriculum from existing vault")
    p_compile.add_argument("topic", help="Topic name (e.g. carpentry)")

    # export
    p_export = sub.add_parser("export", help="Export PDF from existing textbook")
    p_export.add_argument("topic")

    # run (compile + export)
    p_run = sub.add_parser("run", help="Compile + export (no new harvest)")
    p_run.add_argument("topic")

    # harvest
    p_harvest = sub.add_parser("harvest", help="Full harvest + compile + export")
    p_harvest.add_argument("topic")
    p_harvest.add_argument("--min-files",    type=int, default=100, dest="min_files")
    p_harvest.add_argument("--iterations",   type=int, default=5)
    p_harvest.add_argument("--cycles",       type=int, default=1)
    p_harvest.add_argument("--skip-compile", action="store_true",   dest="skip_compile")

    # expand (additive harvest on existing vault)
    p_expand = sub.add_parser("expand", help="Add to existing vault without replacing anything")
    p_expand.add_argument("topic")
    p_expand.add_argument("--min-files",    type=int, default=100, dest="min_files")
    p_expand.add_argument("--iterations",   type=int, default=5)
    p_expand.add_argument("--cycles",       type=int, default=1)
    p_expand.add_argument("--skip-compile", action="store_true",   dest="skip_compile")

    args = parser.parse_args()

    dispatch = {
        "status":  cmd_status,
        "quality": cmd_quality,
        "verify":  cmd_verify,
        "repair":  cmd_repair,
        "smoke":   cmd_smoke,
        "probe":   cmd_probe,
        "compile": cmd_compile,
        "export":  cmd_export,
        "run":     cmd_run,
        "harvest": cmd_harvest,
        "expand":  cmd_expand,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
