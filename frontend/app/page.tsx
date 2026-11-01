"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import { Baseline, Legend, PlanCard, Results, Tree } from "@/components/Panels";
import type { MapHandle } from "@/components/MapView";
import {
  type Base, type Category, type Compare, type ConfirmPayload, type DonePayload, getJSON, type Metrics, type Mode,
  type PlanSpec, postStream, type ScenarioNode,
} from "@/lib/api";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

type Msg = { role: "user" | "agent" | "error"; text: string };

const STAGES: Record<string, string> = {
  interpret: "Reading the question and looking up places",
  plan: "Plan drafted",
  validate: "Checking the plan against the network",
  apply: "Applying edits to a copy of the city",
  compute: "Computing travel times for every cell",
  compare: "Comparing with the real city",
  narrate: "Writing the summary and checking every number",
};

const EXAMPLES = [
  "What if a new clinic opened near Baraki?",
  "What if the main hospital in Bab El Oued closed?",
  "What if Rue Hassiba Ben Bouali were closed to traffic?",
];

export default function Page() {
  const [base, setBase] = useState<Base | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("drive");
  const [category, setCategory] = useState<Category>("hospital");
  const [view, setView] = useState<"swipe" | "diff">("swipe");
  const [current, setCurrent] = useState("factual");
  const [nodes, setNodes] = useState<ScenarioNode[]>([]);
  const [left, setLeft] = useState<Metrics | null>(null);
  const [rightM, setRight] = useState<Metrics | null>(null);
  const [cmpM, setCmp] = useState<Compare | null>(null);
  const [editsM, setEdits] = useState<GeoJSON.FeatureCollection | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [pending, setPending] = useState<ConfirmPayload | null>(null);
  const [original, setOriginal] = useState<PlanSpec | null>(null);
  const [pins, setPins] = useState<{ lon: number; lat: number }[]>([]);
  const [placing, setPlacing] = useState(false);
  const [reach, setReach] = useState<(number | null)[] | null>(null);
  const [reachT, setReachT] = useState(0);
  const mapRef = useRef<MapHandle>(null);
  const [reachAt, setReachAt] = useState<{ lon: number; lat: number; sid: string } | null>(null);

  const say = (m: Msg) => setMsgs((x) => [...x, m]);

  useEffect(() => {
    getJSON<Base>("/api/base").then(setBase).catch((e) => setLoadErr(String(e)));
    getJSON<ScenarioNode[]>("/api/scenarios").then(setNodes).catch(() => {});
  }, []);

  // Layers for the selected scenario, mode and category.
  useEffect(() => {
    if (!base) return;
    const q = { mode, category };
    getJSON<Metrics>("/api/scenarios/factual/metrics", q).then(setLeft);
    if (current === "factual") return;
    getJSON<Metrics>(`/api/scenarios/${current}/metrics`, q).then(setRight);
    getJSON<Compare>(`/api/scenarios/${current}/compare`, q).then(setCmp);
    getJSON<GeoJSON.FeatureCollection>(`/api/scenarios/${current}/edits`).then(setEdits);
  }, [base, current, mode, category]);

  // Growing isochrone from the edited place.
  const playReach = useCallback(async (at: { lon: number; lat: number; sid: string } | null) => {
    if (!at) return;
    const r = await getJSON<{ time_min: (number | null)[] }>(`/api/scenarios/${at.sid}/reach`, { mode, lon: at.lon, lat: at.lat, max_min: 30 });
    setReach(r.time_min);
    const t0 = performance.now();
    const step = (now: number) => {
      const t = ((now - t0) / 2600) * 30;
      setReachT(Math.min(30, t));
      if (t < 30) requestAnimationFrame(step);
      else setTimeout(() => setReach(null), 1800);
    };
    requestAnimationFrame(step);
  }, [mode]);

  const onDone = async (d: DonePayload) => {
    if (d.status === "done" && d.scenario_id) {
      setNodes(await getJSON<ScenarioNode[]>("/api/scenarios"));
      // Show the layer the scenario changes most, e.g. "clinic by foot".
      const top = Object.keys((d as any).facts?.results ?? {})[0];
      if (top) {
        const [cat, , how] = top.split(" ");
        setCategory(cat as Category);
        setMode(how === "car" ? "drive" : "walk");
      }
      setCurrent(d.scenario_id);
      setPins([]);
      setPlacing(false);
      say({ role: "agent", text: d.narrative ?? "Done." });
      const geo = await getJSON<GeoJSON.FeatureCollection>(`/api/scenarios/${d.scenario_id}/edits`);
      const f = geo.features.find((x) => x.geometry.type === "Point") ?? geo.features.find((x) => x.properties?.kind === "new_road");
      if (f) {
        const c = f.geometry.type === "Point" ? (f.geometry as GeoJSON.Point).coordinates : (f.geometry as GeoJSON.LineString).coordinates[0];
        // A closed facility shows the area it used to serve, so draw its reach in the real city.
        const sid = f.properties?.kind === "closed_facility" ? "factual" : d.scenario_id;
        const at = { lon: c[0], lat: c[1], sid };
        setReachAt(at);
        setTimeout(() => playReach(at), 400);
      } else setReachAt(null);
    } else if (d.status === "cancelled") say({ role: "agent", text: "Cancelled. The city is unchanged." });
    else say({ role: "error", text: `I could not build a valid plan. ${(d.problems ?? []).map((p) => p.message).join(" ")}` });
  };

  const run = async (path: string, body: unknown) => {
    setBusy(true);
    try {
      await postStream(path, body, (ev, data) => {
        if (ev === "progress") setStage(data.stage);
        else if (ev === "confirm") {
          setPending(data);
          setOriginal(data.plan);
          setStage(null);
        } else if (ev === "done") onDone(data);
        else if (ev === "error") say({ role: "error", text: data.message });
      });
    } catch (e) {
      say({ role: "error", text: `Could not reach the backend: ${e}` });
    } finally {
      setBusy(false);
      setStage(null);
    }
  };

  const ask = (text: string) => {
    if (!text.trim() || busy) return;
    say({ role: "user", text });
    setInput("");
    setPending(null);
    run("/api/ask", { request: text, current_id: current, clicks: pins });
  };

  const decide = (action: "approve" | "edit" | "cancel") => {
    if (!pending) return;
    const p = pending;
    setPending(null);
    run("/api/confirm", { thread_id: p.thread_id, action, plan: action === "edit" ? p.plan : undefined });
  };

  const exportPng = () => {
    const snap = mapRef.current?.snapshot();
    if (!snap) return;
    const W = snap.width;
    const pad = Math.round(W * 0.025);
    const fs = Math.round(W * 0.014);
    const lines: string[] = [];
    const s = cmp?.summary;
    const sc = nodes.find((n) => n.id === current);
    if (s) {
      lines.push(`People newly within 15 min (net): ${(s.pop_newly_within_15 - s.pop_no_longer_within_15).toLocaleString()}`);
      lines.push(`Median trip: ${s.median_min.factual} → ${s.median_min.counterfactual} min   Gini: ${s.gini.factual} → ${s.gini.counterfactual}`);
      lines.push(`Faster: ${s.pop_gaining.toLocaleString()} people   Slower: ${s.pop_losing.toLocaleString()} people`);
    }
    const story = cmp?.narrative ?? "";
    const out = document.createElement("canvas");
    const ctx0 = out.getContext("2d")!;
    ctx0.font = `${fs}px sans-serif`;
    const wrap = (t: string) => {
      const words = t.split(" ");
      const res: string[] = [];
      let cur = "";
      for (const w of words) {
        if (ctx0.measureText(cur + " " + w).width > W - 2 * pad) { res.push(cur); cur = w; } else cur = cur ? cur + " " + w : w;
      }
      if (cur) res.push(cur);
      return res;
    };
    const storyLines = wrap(story);
    const head = fs * 3.2;
    const foot = pad + (lines.length + storyLines.length + 2) * fs * 1.5;
    out.width = W;
    out.height = head + snap.height + foot;
    const ctx = out.getContext("2d")!;
    ctx.fillStyle = "#0e1116";
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.fillStyle = "#e6e8eb";
    ctx.font = `600 ${fs * 1.4}px sans-serif`;
    ctx.fillText(`Algiers: ${sc?.summary || "real city"}`, pad, fs * 2);
    ctx.font = `${fs * 0.9}px sans-serif`;
    ctx.fillStyle = "#7d8590";
    ctx.fillText(`${category} by ${mode === "drive" ? "car" : "foot"}, ${view === "diff" ? "change vs real city" : "real city (left) vs scenario (right)"}`, pad, fs * 2.9);
    ctx.drawImage(snap, 0, head);
    let y = head + snap.height + pad + fs;
    ctx.fillStyle = "#e6e8eb";
    ctx.font = `${fs}px sans-serif`;
    for (const l of [...lines, "", ...storyLines]) { ctx.fillText(l, pad, y); y += fs * 1.5; }
    ctx.fillStyle = "#7d8590";
    ctx.font = `${fs * 0.75}px sans-serif`;
    ctx.fillText("OSM (Geofabrik 2026-10-31), WorldPop 2025, free-flow speeds. Counterfactual Accessibility Lab.", pad, out.height - pad / 2);
    const a = document.createElement("a");
    a.download = `algiers-${current}-${mode}-${category}.png`;
    a.href = out.toDataURL("image/png");
    a.click();
  };

  const pop = base?.cells.pop ?? [];
  // Scenario layers only apply while a scenario is selected.
  const right = current === "factual" ? null : rightM;
  const cmp = current === "factual" ? null : cmpM;
  const edits = current === "factual" ? null : editsM;
  const scenario = nodes.find((n) => n.id === current);

  return (
    <main className="grid h-dvh grid-cols-[340px_1fr_340px] overflow-hidden bg-[var(--bg)] text-[var(--fg)] max-lg:grid-cols-1 max-lg:grid-rows-[auto_60vh_auto] max-lg:overflow-auto print:grid-cols-[1fr_320px]">
      {/* ---------- left: chat ---------- */}
      <aside className="flex min-h-0 flex-col border-r border-[var(--line)] bg-[var(--panel)] print:hidden">
        <header className="border-b border-[var(--line)] px-4 py-3">
          <h1 className="text-sm font-semibold tracking-tight">Counterfactual Accessibility Lab</h1>
          <p className="text-xs text-[var(--muted)]">What if Algiers changed? Ask, check the plan, see who gains and who loses.</p>
        </header>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
          {msgs.length === 0 && (
            <div className="space-y-2">
              <p className="text-xs text-[var(--muted)]">Try one of these:</p>
              {EXAMPLES.map((e) => (
                <button key={e} onClick={() => ask(e)} disabled={busy || !base}
                  className="block w-full rounded-md border border-[var(--line)] px-3 py-2 text-left text-sm text-[var(--fg-2)] transition-colors hover:border-[var(--accent)]/60 hover:text-[var(--fg)]">
                  {e}
                </button>
              ))}
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={m.role === "user" ? "ml-6 rounded-lg bg-[var(--panel-2)] px-3 py-2 text-sm"
              : m.role === "error" ? "text-sm text-[var(--bad)]" : "text-sm leading-relaxed text-[var(--fg-2)]"}>
              {m.text}
            </div>
          ))}
          {pending && original && (
            <PlanCard pending={pending} original={original} busy={busy}
              onChange={(plan) => setPending({ ...pending, plan })}
              onApprove={(edited) => decide(edited ? "edit" : "approve")}
              onCancel={() => decide("cancel")} />
          )}
          {stage && (
            <div className="flex items-center gap-2 text-xs text-[var(--accent)]" aria-live="polite">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
              {STAGES[stage] ?? stage}
            </div>
          )}
        </div>
        <div className="border-t border-[var(--line)] p-3">
          <div className="mb-2 flex items-center gap-2 text-xs">
            <button onClick={() => setPlacing((v) => !v)}
              className={`rounded-md border px-2 py-1 ${placing ? "border-[var(--pin)] text-[var(--pin)]" : "border-[var(--line)] text-[var(--muted)]"}`}>
              {placing ? "Click the map to drop pins" : "Drop a pin"}
            </button>
            {pins.length > 0 && (
              <button onClick={() => setPins([])} className="text-[var(--muted)] underline-offset-2 hover:underline">
                clear {pins.length} pin{pins.length > 1 ? "s" : ""}
              </button>
            )}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="flex gap-2">
            <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy || !base}
              placeholder={pins.length ? "e.g. what if a pharmacy opened at the pin?" : "What if..."}
              className="field flex-1 py-2" aria-label="Your what-if question" />
            <button disabled={busy || !input.trim()} className="btn-primary">Ask</button>
          </form>
          <p className="mt-2 text-[11px] text-[var(--muted-2)]">
            {current === "factual" ? "Questions start from the real city." : `Follow-ups can build on scenario ${current}.`}
          </p>
        </div>
      </aside>

      {/* ---------- centre: map ---------- */}
      <section className="relative min-h-0">
        {base ? (
          <MapView ref={mapRef} base={base} left={left} right={right} delta={cmp?.delta_min ?? null} view={view}
            edits={edits} reach={reach} reachT={reachT} pins={pins} placing={placing}
            onPlace={(p) => setPins((x) => [...x, p])}
            leftLabel="Real city" rightLabel={scenario && current !== "factual" ? `Scenario ${current}` : "Scenario"} />
        ) : (
          <div className="grid h-full place-items-center text-sm text-[var(--muted)]">
            {loadErr ? `Backend not reachable (${loadErr}). Start it with: uv run uvicorn backend.api.app:app` : "Loading Algiers…"}
          </div>
        )}
        <div className="absolute left-3 top-3 z-10 flex flex-wrap gap-2 print:hidden">
          <Seg value={mode} onChange={(v) => setMode(v as Mode)} options={[["drive", "Car"], ["walk", "Walking"]]} />
          <Seg value={category} onChange={(v) => setCategory(v as Category)}
            options={[["hospital", "Hospital"], ["clinic", "Clinic"], ["pharmacy", "Pharmacy"], ["school", "School"]]} />
          {current !== "factual" && <Seg value={view} onChange={(v) => setView(v as "swipe" | "diff")} options={[["swipe", "Swipe"], ["diff", "Difference"]]} />}
        </div>
        <div className="absolute bottom-8 left-3 z-10"><Legend view={view} hasScenario={current !== "factual"} /></div>
      </section>

      {/* ---------- right: results ---------- */}
      <aside className="min-h-0 space-y-5 overflow-y-auto border-l border-[var(--line)] bg-[var(--panel)] px-4 py-4">
        {current === "factual" || !cmp
          ? left && <Baseline m={left} mode={mode} category={category} />
          : <Results c={cmp} pop={pop} mode={mode} category={category} />}
        {current !== "factual" && (
          <div className="flex gap-2 print:hidden">
            {reachAt && <button onClick={() => playReach(reachAt)} className="btn-ghost flex-1">Replay reach</button>}
            <button onClick={exportPng} className="btn-ghost flex-1">Export PNG</button>
            <button onClick={() => window.print()} className="btn-ghost flex-1">PDF</button>
          </div>
        )}
        <section className="print:hidden">
          <h2 className="panel-h">Scenarios</h2>
          <Tree nodes={nodes} current={current} onPick={setCurrent} />
        </section>
      </aside>
    </main>
  );
}

function Seg({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <div className="flex rounded-md border border-[var(--line)] bg-[var(--panel)]/90 p-0.5 text-xs shadow-lg backdrop-blur" role="radiogroup">
      {options.map(([v, label]) => (
        <button key={v} role="radio" aria-checked={value === v} onClick={() => onChange(v)}
          className={`rounded px-2.5 py-1 transition-colors ${value === v ? "bg-[var(--accent)]/20 text-[var(--fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}
