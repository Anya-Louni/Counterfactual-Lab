"use client";

import type { Category, Compare, ConfirmPayload, EditSpec, Metrics, Mode, PlanSpec, ScenarioNode } from "@/lib/api";
import { DELTA_COLORS, DELTA_LABELS, deltaColor, EDIT_COLORS, TIME_COLORS, TIME_LABELS } from "@/lib/colors";

const fmt = (n: number | null | undefined, d = 0) =>
  n === null || n === undefined || Number.isNaN(n) ? "n/a" : n.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const signed = (n: number, d = 1) => (n > 0 ? "+" : n < 0 ? "−" : "") + fmt(Math.abs(n), d);
const rgb = (c: number[]) => `rgb(${c[0]},${c[1]},${c[2]})`;

/* ---------------- plan card ---------------- */

const OP_LABEL: Record<EditSpec["op"], string> = {
  add_facility: "Open facility",
  remove_facility: "Close facility",
  add_road: "New road",
  remove_road: "Close road",
  road_speed_upgrade: "Road speed upgrade",
};

export function PlanCard({
  pending,
  original,
  onChange,
  onApprove,
  onCancel,
  busy,
}: {
  pending: ConfirmPayload;
  original: PlanSpec;
  onChange: (plan: PlanSpec) => void;
  onApprove: (edited: boolean) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const plan = pending.plan;
  const placeName = (ref?: string | null) => (ref ? pending.places[ref]?.name ?? ref : "");
  const set = (i: number, patch: Partial<EditSpec>) =>
    onChange({ ...plan, edits: plan.edits.map((e, j) => (j === i ? { ...e, ...patch } : e)) });
  const drop = (i: number) => onChange({ ...plan, edits: plan.edits.filter((_, j) => j !== i) });
  const edited = JSON.stringify(plan) !== JSON.stringify(original);

  return (
    <div className="rounded-lg border border-[var(--accent)]/40 bg-[var(--panel-2)] p-3">
      <div className="mb-1 text-[11px] uppercase tracking-wider text-[var(--accent)]">Plan, waiting for you</div>
      <p className="mb-3 text-sm text-[var(--fg)]">{plan.summary}</p>
      <div className="mb-2 text-xs text-[var(--muted)]">
        Starts from: {pending.parent_id === "factual" ? "the real city" : `scenario ${pending.parent_id}`}
      </div>
      <ul className="space-y-2">
        {plan.edits.map((e, i) => (
          <li key={i} className="rounded-md border border-[var(--line)] bg-[var(--panel)] p-2.5 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium">{OP_LABEL[e.op]}</span>
              <button onClick={() => drop(i)} className="text-xs text-[var(--muted)] hover:text-[var(--bad)]" aria-label="Remove this edit">
                remove
              </button>
            </div>
            <div className="mt-1 space-y-1.5 text-xs text-[var(--muted)]">
              {e.op === "add_facility" && (
                <>
                  <Row label="Type">
                    <select className="field" value={e.category ?? ""} onChange={(ev) => set(i, { category: ev.target.value as Category })}>
                      {["hospital", "clinic", "pharmacy", "school"].map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </Row>
                  <Row label="At">{placeName(e.place)}</Row>
                </>
              )}
              {e.op === "remove_facility" && <Row label="Facility">{placeNameById(pending, e.facility_id)}</Row>}
              {e.op === "add_road" && (
                <>
                  <Row label="From">{placeName(e.start)}</Row>
                  <Row label="To">{placeName(e.end)}</Row>
                  <Row label="Speed">
                    <input className="field w-16" type="number" min={5} max={130} value={e.speed_kph ?? 40}
                      onChange={(ev) => set(i, { speed_kph: Number(ev.target.value) })} /> km/h
                  </Row>
                </>
              )}
              {(e.op === "remove_road" || e.op === "road_speed_upgrade") && (
                <>
                  {e.road && <Row label="Road">{placeName(e.road)}</Row>}
                  {e.place && (
                    <Row label="Area">
                      {placeName(e.place)}, radius
                      <input className="field w-16" type="number" min={50} max={2000} step={50} value={e.radius_m ?? 300}
                        onChange={(ev) => set(i, { radius_m: Number(ev.target.value) })} /> m
                    </Row>
                  )}
                </>
              )}
              {e.op === "road_speed_upgrade" && (
                <Row label="Speed x">
                  <input className="field w-16" type="number" min={1.1} max={5} step={0.1} value={e.factor ?? 1.5}
                    onChange={(ev) => set(i, { factor: Number(ev.target.value), speed_kph: null })} />
                </Row>
              )}
              {(e.op === "add_road" || e.op === "remove_road") && (
                <Row label="For">
                  {(["drive", "walk"] as Mode[]).map((m) => {
                    const on = (e.modes ?? ["drive", "walk"]).includes(m);
                    return (
                      <label key={m} className="mr-2 inline-flex items-center gap-1">
                        <input type="checkbox" checked={on} onChange={() => {
                          const cur = e.modes ?? ["drive", "walk"];
                          const next = on ? cur.filter((x) => x !== m) : [...cur, m];
                          if (next.length) set(i, { modes: next });
                        }} />
                        {m === "drive" ? "cars" : "walking"}
                      </label>
                    );
                  })}
                </Row>
              )}
            </div>
          </li>
        ))}
      </ul>
      {pending.warnings.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-[var(--warn)]">
          {pending.warnings.map((w, i) => <li key={i}>{w.message}</li>)}
        </ul>
      )}
      <div className="mt-3 flex gap-2">
        <button disabled={busy || !plan.edits.length} onClick={() => onApprove(edited)} className="btn-primary flex-1">
          {edited ? "Check and run" : "Run scenario"}
        </button>
        <button disabled={busy} onClick={onCancel} className="btn-ghost">Cancel</button>
      </div>
    </div>
  );
}

function placeNameById(p: ConfirmPayload, id?: string | null) {
  const hit = Object.values(p.places).find((x) => x.id === id);
  return hit ? hit.name : id ?? "";
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-14 shrink-0 text-[var(--muted-2)]">{label}</span>
      <span className="flex flex-wrap items-center gap-1 text-[var(--fg-2)]">{children}</span>
    </div>
  );
}

/* ---------------- results ---------------- */

export function Baseline({ m, mode, category }: { m: Metrics; mode: Mode; category: Category }) {
  const s = m.summary;
  return (
    <section>
      <h2 className="panel-h">Real city, {category} by {mode === "drive" ? "car" : "foot"}</h2>
      <div className="grid grid-cols-2 gap-2">
        <Kpi label="Median trip" value={`${fmt(s.median_min, 1)} min`} />
        <Kpi label="Within 15 min" value={fmt(s.pop_within_15)} sub="people" />
        <Kpi label="Gini of travel time" value={fmt(s.gini, 3)} sub="0 = everyone equal" />
        <Kpi label="90th / 10th pct" value={`${fmt(s.p90_p10, 1)}×`} />
      </div>
      <p className="mt-3 text-xs leading-relaxed text-[var(--muted)]">
        Ask a what-if question on the left, or place a pin on the map first. Nothing changes until you approve the plan.
      </p>
    </section>
  );
}

export function Results({ c, pop, mode, category }: { c: Compare; pop: number[]; mode: Mode; category: Category }) {
  const s = c.summary;
  if (!s) {
    return (
      <section>
        <h2 className="panel-h">{category} by {mode === "drive" ? "car" : "foot"}</h2>
        <p className="text-sm text-[var(--muted)]">
          No one&apos;s trip to the nearest {category} changes by half a minute or more in this scenario.
          {c.changed.length > 0 && <> Changes show up for: {c.changed.map((k) => k.replace(".", ", ")).join("; ")}.</>}
        </p>
      </section>
    );
  }
  const net = s.pop_newly_within_15 - s.pop_no_longer_within_15;
  return (
    <section className="space-y-4">
      <div>
        <h2 className="panel-h">{category} by {mode === "drive" ? "car" : "foot"}, scenario vs real city</h2>
        <div className="grid grid-cols-2 gap-2">
          <Kpi label="Newly within 15 min" value={signed(net, 0)} sub="people, net" tone={net > 0 ? "good" : net < 0 ? "bad" : undefined} />
          <Kpi label="Median trip" value={`${signed(s.median_min.change)} min`} sub={`${fmt(s.median_min.factual, 1)} → ${fmt(s.median_min.counterfactual, 1)}`} />
          <Kpi label="Gini" value={signed(s.gini.change, 3)} sub={`${fmt(s.gini.factual, 3)} → ${fmt(s.gini.counterfactual, 3)}`}
            tone={s.gini.change < 0 ? "good" : s.gini.change > 0 ? "bad" : undefined} />
          <Kpi label="Faster / slower" value={`${fmt(s.pop_gaining)} / ${fmt(s.pop_losing)}`} sub="people" />
        </div>
      </div>
      <Histogram delta={c.delta_min} pop={pop} />
      {c.narrative && (
        <div>
          <h3 className="panel-h">What it means</h3>
          <p className="text-sm leading-relaxed text-[var(--fg-2)]">{c.narrative}</p>
          <p className="mt-1 text-[11px] text-[var(--muted-2)]">Every number above was checked against the computed results.</p>
        </div>
      )}
      {c.top && (c.top.gaining_communes.length > 0 || c.top.losing_communes.length > 0) && (
        <div>
          <h3 className="panel-h">Communes most affected</h3>
          <table className="w-full text-xs">
            <tbody>
              {[...c.top.gaining_communes, ...c.top.losing_communes].map((r) => (
                <tr key={r.commune + r.mean_change_min} className="border-t border-[var(--line)]">
                  <td className="py-1.5 text-[var(--fg-2)]">{r.commune}</td>
                  <td className="text-right text-[var(--muted)]">{fmt(r.pop_affected)} people</td>
                  <td className="w-20 text-right tabular-nums" style={{ color: r.mean_change_min < 0 ? "var(--good)" : "var(--bad)" }}>
                    {signed(r.mean_change_min)} min
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-md border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2">
      <div className="text-[11px] text-[var(--muted)]">{label}</div>
      <div className="text-lg font-semibold tabular-nums" style={{ color: tone ? `var(--${tone})` : "var(--fg)" }}>{value}</div>
      {sub && <div className="text-[11px] text-[var(--muted-2)]">{sub}</div>}
    </div>
  );
}

function Histogram({ delta, pop }: { delta: (number | null)[]; pop: number[] }) {
  const edges = [-Infinity, -10, -5, -3, -2, -1, -0.5, 0.5, 1, 2, 3, 5, 10, Infinity];
  const sums = new Array(edges.length - 1).fill(0);
  delta.forEach((d, i) => {
    if (d === null) return;
    const b = edges.findIndex((e, j) => d >= e && d < edges[j + 1]);
    if (b >= 0) sums[b] += pop[i];
  });
  sums[6] = 0; // the no-change bin would dwarf everything else
  const max = Math.max(...sums, 1);
  const mid = (j: number) => (j < 6 ? (edges[j + 1] === -0.5 ? -1 : edges[j + 1] - 0.01) : j > 6 ? edges[j] + 0.01 : 0);
  return (
    <div>
      <h3 className="panel-h">People by change in trip time</h3>
      <div className="flex h-24 items-end gap-[3px]" role="img" aria-label="Histogram of people by change in minutes">
        {sums.map((v, j) => (
          <div key={j} className="flex-1 rounded-t-sm" title={`${fmt(v)} people`}
            style={{ height: `${(100 * v) / max}%`, minHeight: v > 0 ? 2 : 0, background: j === 6 ? "transparent" : rgb(deltaColor(mid(j)) as number[]) }} />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-[var(--muted-2)]">
        <span>10+ min faster</span><span>no change</span><span>10+ min slower</span>
      </div>
    </div>
  );
}

/* ---------------- scenario tree ---------------- */

export function Tree({ nodes, current, onPick }: { nodes: ScenarioNode[]; current: string; onPick: (id: string) => void }) {
  const kids = (id: string | null) => nodes.filter((n) => n.parent === id);
  const render = (n: ScenarioNode, depth: number): React.ReactNode => (
    <li key={n.id}>
      <button onClick={() => onPick(n.id)}
        className={`w-full rounded-md px-2 py-1.5 text-left text-xs transition-colors ${current === n.id ? "bg-[var(--accent)]/15 text-[var(--fg)]" : "text-[var(--muted)] hover:bg-[var(--panel-2)]"}`}
        style={{ paddingLeft: 8 + depth * 14 }}>
        <span className="mr-1.5 font-mono text-[10px] text-[var(--muted-2)]">{n.id}</span>
        {n.id === "factual" ? "Real city" : n.summary || n.request}
      </button>
      {kids(n.id).length > 0 && <ul>{kids(n.id).map((k) => render(k, depth + 1))}</ul>}
    </li>
  );
  return <ul className="space-y-0.5">{kids(null).map((n) => render(n, 0))}</ul>;
}

/* ---------------- legend ---------------- */

export function Legend({ view, hasScenario }: { view: "swipe" | "diff"; hasScenario: boolean }) {
  const diff = view === "diff" && hasScenario;
  const colors = diff ? DELTA_COLORS : TIME_COLORS;
  const labels = diff ? DELTA_LABELS : TIME_LABELS;
  return (
    <div className="rounded-md border border-[var(--line)] bg-[var(--panel)]/90 p-2.5 text-[11px] backdrop-blur">
      <div className="mb-1.5 text-[var(--muted)]">{diff ? "Change in minutes to nearest" : "Minutes to nearest"}</div>
      <div className="space-y-1">
        {colors.map((c, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="h-2.5 w-4 rounded-[2px]" style={{ background: rgb(c) }} />
            <span className="text-[var(--fg-2)]">{labels[i]}</span>
          </div>
        ))}
      </div>
      {hasScenario && (
        <div className="mt-2 space-y-1 border-t border-[var(--line)] pt-2">
          <Sym color={EDIT_COLORS.new_road} label="new road" />
          <Sym color={EDIT_COLORS.closed_road} label="closed road" dashed />
          <Sym color={EDIT_COLORS.upgraded_road} label="road speed upgrade" />
          <div className="flex items-center gap-2 text-[var(--fg-2)]"><span className="w-4 text-center font-bold text-[var(--accent)]">+</span>new facility</div>
          <div className="flex items-center gap-2 text-[var(--fg-2)]"><span className="w-4 text-center font-bold text-[var(--bad)]">×</span>closed facility</div>
        </div>
      )}
      <div className="mt-2 flex items-center gap-2 border-t border-[var(--line)] pt-2 text-[var(--fg-2)]">
        <span className="h-2 w-2 rounded-full border border-black bg-white" />existing facility
      </div>
    </div>
  );
}

function Sym({ color, label, dashed }: { color: number[]; label: string; dashed?: boolean }) {
  return (
    <div className="flex items-center gap-2 text-[var(--fg-2)]">
      <span className="h-0 w-4" style={{ borderTop: `2.5px ${dashed ? "dashed" : "solid"} ${rgb(color)}` }} />
      {label}
    </div>
  );
}
