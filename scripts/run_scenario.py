"""Ask one what-if question from the command line. Approves the plan after printing it.

uv run python scripts/run_scenario.py "What if a new clinic opened near Baraki?"
"""

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agent.graph import build, run_until_pause  # noqa: E402
from backend.lab import Lab  # noqa: E402


def main():
    load_dotenv()
    question = " ".join(sys.argv[1:]) or "What if a new clinic opened near Baraki?"
    lab = Lab()
    g = build(lab)
    cfg = {"configurable": {"thread_id": "cli"}}
    t0 = time.perf_counter()
    out = run_until_pause(g, cfg, {"request": question})
    t_plan = time.perf_counter() - t0
    if "final" in out:
        print("stopped:", out["final"].get("status"), json.dumps(out["final"].get("problems"), indent=1))
        return
    print("PLAN", json.dumps(out["interrupt"]["plan"], indent=1, ensure_ascii=False))
    print("EDITS", json.dumps(out["interrupt"]["edits"], ensure_ascii=False)[:600])
    t1 = time.perf_counter()
    final = run_until_pause(g, cfg, Command(resume={"action": "approve"}))["final"]
    t_run = time.perf_counter() - t1
    print("FACTS", json.dumps(final["facts"], indent=1, ensure_ascii=False))
    print("NARRATIVE:", final["narrative"])
    for p in final["provenance"]:
        if "seconds" in p:
            print("  ", p.get("node") or p.get("llm"), p["seconds"], p.get("parts", ""), p.get("unsupported_numbers", ""))
    print(f"plan {t_plan:.1f} s, apply to narrative {t_run:.1f} s")


if __name__ == "__main__":
    main()
