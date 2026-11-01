// Travel time: magma, same bins as the static maps in docs/. Bright means slow, so gaps in
// access stand out on the dark basemap.
export const TIME_BINS = [5, 10, 15, 30, 60, Infinity];
export const TIME_LABELS = ["< 5", "5 to 10", "10 to 15", "15 to 30", "30 to 60", "60 +"];
export const TIME_COLORS: [number, number, number][] = [
  [51, 16, 103],
  [118, 33, 129],
  [186, 56, 120],
  [242, 100, 92],
  [254, 178, 122],
  [252, 253, 191],
];

export function timeColor(t: number | null): [number, number, number, number] {
  if (t === null) return [0, 0, 0, 0];
  const i = TIME_BINS.findIndex((b) => t < b);
  return [...TIME_COLORS[i === -1 ? TIME_COLORS.length - 1 : i], 215];
}

// Change in minutes: blue for faster, orange for slower (safe for common colour blindness).
// Brighter means a bigger change; no change is left transparent.
export const DELTA_BINS = [-5, -2, -0.5, 0.5, 2, 5];
export const DELTA_LABELS = ["5+ faster", "2 to 5 faster", "0.5 to 2 faster", "0.5 to 2 slower", "2 to 5 slower", "5+ slower"];
export const DELTA_COLORS: [number, number, number][] = [
  [158, 211, 255],
  [74, 144, 217],
  [38, 82, 140],
  [133, 74, 22],
  [224, 130, 20],
  [255, 194, 122],
];

export function deltaColor(d: number | null): [number, number, number, number] {
  if (d === null || Math.abs(d) < 0.5) return [0, 0, 0, 0];
  const c = d <= -5 ? 0 : d <= -2 ? 1 : d < 0 ? 2 : d < 2 ? 3 : d < 5 ? 4 : 5;
  return [...DELTA_COLORS[c], 230];
}

export const EDIT_COLORS = {
  new_road: [94, 234, 212] as [number, number, number],
  closed_road: [248, 81, 73] as [number, number, number],
  faster_road: [250, 204, 21] as [number, number, number],
  slower_road: [248, 81, 73] as [number, number, number],
  new_facility: [94, 234, 212] as [number, number, number],
  closed_facility: [248, 81, 73] as [number, number, number],
};

export const REACH_COLOR: [number, number, number] = [94, 234, 212];
