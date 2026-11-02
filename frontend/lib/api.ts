export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Mode = "drive" | "walk";
export type Category = "hospital" | "clinic" | "pharmacy" | "school";

export type Facility = { id: string; category: Category; name: string | null; lon: number; lat: number };

export type Base = {
  boundary: GeoJSON.FeatureCollection;
  communes: GeoJSON.FeatureCollection;
  cells: { h3: string[]; pop: number[]; commune: string[] };
  facilities: Facility[];
};

export type Metrics = {
  time_min: (number | null)[];
  cum: number[];
  sfca: number[];
  summary: Record<string, number>;
  facilities: Facility[];
};

export type Change = { factual: number; counterfactual: number; change: number };
export type CommuneRow = { commune: string; pop_affected: number; mean_change_min: number };

export type Compare = {
  delta_min: (number | null)[];
  summary: null | (Record<string, Change> & {
    pop_gaining: number;
    pop_losing: number;
    pop_newly_within_15: number;
    pop_no_longer_within_15: number;
    largest_gain_min: number;
    largest_loss_min: number;
  });
  top: null | { gaining_communes: CommuneRow[]; losing_communes: CommuneRow[] };
  changed: string[];
  narrative: string | null;
};

export type ScenarioNode = {
  id: string;
  parent: string | null;
  log: string[];
  edits: Record<string, unknown>[];
  summary: string;
  request: string | null;
  narrative: string | null;
};

export type EditSpec = {
  op: "add_facility" | "remove_facility" | "add_road" | "remove_road" | "road_speed_upgrade";
  category?: Category | null;
  place?: string | null;
  radius_m?: number | null;
  road?: string | null;
  start?: string | null;
  end?: string | null;
  facility_id?: string | null;
  speed_kph?: number | null;
  factor?: number | null;
  modes?: Mode[] | null;
  name?: string | null;
};

export type PlanSpec = { summary: string; base: "current" | "factual"; edits: EditSpec[] };

export type Place = { kind: string; name: string; lon: number; lat: number; id?: string; category?: string };

export type ConfirmPayload = {
  thread_id: string;
  plan: PlanSpec;
  edits: Record<string, unknown>[];
  parent_id: string;
  warnings: { message: string }[];
  places: Record<string, Place>;
};

export type DonePayload = {
  thread_id: string;
  status: "done" | "cancelled" | "failed";
  scenario_id: string | null;
  narrative: string | null;
  problems: { message: string; severity: string }[] | null;
  plan: PlanSpec | null;
};

export async function getJSON<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const q = params ? "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])) : "";
  const r = await fetch(`${API}${path}${q}`);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

/** POST and read server-sent events until the stream closes. */
export async function postStream(
  path: string,
  body: unknown,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok || !r.body) throw new Error(`${r.status} ${await r.text()}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let ev = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) ev = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(ev, JSON.parse(data));
    }
  }
}
