"use client";

import { PathStyleExtension } from "@deck.gl/extensions";
import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { GeoJsonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import maplibregl from "maplibre-gl";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

import type { Base, Facility, Metrics } from "@/lib/api";
import { deltaColor, EDIT_COLORS, REACH_COLOR, timeColor } from "@/lib/colors";

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";
const LABELS = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const START = { center: [3.06, 36.71] as [number, number], zoom: 10.3 };

export type MapHandle = { snapshot: () => HTMLCanvasElement | null };

type Props = {
  base: Base;
  left: Metrics | null;
  right: Metrics | null;
  delta: (number | null)[] | null;
  view: "swipe" | "diff";
  edits: GeoJSON.FeatureCollection | null;
  reach: (number | null)[] | null;
  reachT: number;
  pins: { lon: number; lat: number }[];
  placing: boolean;
  onPlace: (p: { lon: number; lat: number }) => void;
  leftLabel: string;
  rightLabel: string;
};

type Cell = { h: string; i: number };

function makeMap(el: HTMLDivElement, labels: boolean) {
  return new maplibregl.Map({
    container: el,
    style: labels ? LABELS : STYLE,
    ...START,
    attributionControl: false,
    canvasContextAttributes: { preserveDrawingBuffer: true, antialias: true },
  });
}

function facilityLayer(id: string, f: Facility[]) {
  return new ScatterplotLayer<Facility>({
    id,
    data: f,
    getPosition: (d) => [d.lon, d.lat],
    radiusUnits: "pixels",
    getRadius: 3.2,
    getFillColor: [255, 255, 255, 235],
    getLineColor: [14, 17, 22, 255],
    lineWidthUnits: "pixels",
    getLineWidth: 1,
    stroked: true,
    pickable: true,
  });
}

const MapView = forwardRef<MapHandle, Props>(function MapView(p, ref) {
  const leftEl = useRef<HTMLDivElement>(null);
  const rightEl = useRef<HTMLDivElement>(null);
  const maps = useRef<{ l?: maplibregl.Map; r?: maplibregl.Map; ol?: MapboxOverlay; or?: MapboxOverlay }>({});
  const [swipe, setSwipe] = useState(50);
  const [labelId, setLabelId] = useState<string | undefined>(undefined);
  const wrap = useRef<HTMLDivElement>(null);
  const placingRef = useRef(p.placing);
  placingRef.current = p.placing;
  const onPlaceRef = useRef(p.onPlace);
  onPlaceRef.current = p.onPlace;

  const cells: Cell[] = useMemo(() => p.base.cells.h3.map((h, i) => ({ h, i })), [p.base]);
  const hasRight = p.right !== null;
  const split = hasRight && p.view === "swipe";

  // Create both maps once and keep their cameras in sync.
  useEffect(() => {
    const l = makeMap(leftEl.current!, true);
    const r = makeMap(rightEl.current!, true);
    l.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 120 }), "bottom-right");
    l.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "OSM, WorldPop, CARTO" }));
    r.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 120 }), "bottom-right");
    let syncing = false;
    const sync = (from: maplibregl.Map, to: maplibregl.Map) => () => {
      if (syncing) return;
      syncing = true;
      to.jumpTo({ center: from.getCenter(), zoom: from.getZoom(), bearing: from.getBearing(), pitch: from.getPitch() });
      syncing = false;
    };
    // Data layers go under the basemap's first label layer so place names stay readable.
    l.on("load", () => setLabelId(l.getStyle().layers.find((x) => x.type === "symbol")?.id));
    l.fitBounds([[2.79, 36.57], [3.39, 36.83]], { padding: 24, duration: 0 }); // the wilaya
    l.on("move", sync(l, r));
    r.on("move", sync(r, l));
    const click = (e: maplibregl.MapMouseEvent) => {
      if (placingRef.current) onPlaceRef.current({ lon: e.lngLat.lng, lat: e.lngLat.lat });
    };
    l.on("click", click);
    r.on("click", click);
    const tooltip = ({ object }: { object?: any }) => {
      if (!object) return null;
      if (object.category) return { text: `${object.name ?? "(unnamed)"}\n${object.category}` };
      if (typeof object.i === "number") {
        const i = object.i;
        const pop = Math.round(p.base.cells.pop[i]).toLocaleString();
        return { text: `${p.base.cells.commune[i] ?? ""}\n${pop} people` };
      }
      return null;
    };
    const ol = new MapboxOverlay({ interleaved: true, layers: [], getTooltip: tooltip as any });
    const or = new MapboxOverlay({ interleaved: true, layers: [], getTooltip: tooltip as any });
    l.addControl(ol as any);
    r.addControl(or as any);
    maps.current = { l, r, ol, or };
    return () => {
      l.remove();
      r.remove();
    };
  }, [p.base]);

  useEffect(() => {
    maps.current.l?.resize();
    maps.current.r?.resize();
  }, [hasRight, p.view]);

  useEffect(() => {
    const canvas = (m?: maplibregl.Map) => m?.getCanvasContainer();
    for (const m of [maps.current.l, maps.current.r]) {
      const c = canvas(m);
      if (c) c.style.cursor = p.placing ? "crosshair" : "";
    }
  }, [p.placing]);

  // Layers.
  useEffect(() => {
    const { ol, or } = maps.current;
    if (!ol || !or) return;
    // Each map needs its own layer instances: deck.gl layers belong to one WebGL context.
    const outline = (side: string) => new GeoJsonLayer({
      id: `boundary-${side}`,
      data: p.base.boundary,
      stroked: true,
      filled: false,
      getLineColor: [125, 133, 144, 200],
      lineWidthUnits: "pixels",
      getLineWidth: 1.2,
    });
    const communes = (side: string) => new GeoJsonLayer({
      id: `communes-${side}`,
      data: p.base.communes,
      stroked: true,
      filled: false,
      getLineColor: [14, 17, 22, 170],
      lineWidthUnits: "pixels",
      getLineWidth: 0.6,
      ...({ beforeId: labelId } as object),
    });
    const hex = (id: string, values: (number | null)[] | null, color: (v: number | null) => number[]) =>
      new H3HexagonLayer<Cell>({
        id,
        data: cells,
        getHexagon: (d) => d.h,
        getFillColor: (d) => color(values ? values[d.i] : null) as any,
        extruded: false,
        stroked: false,
        coverage: 0.96,
        pickable: true,
        ...({ beforeId: labelId } as object), // read by MapboxOverlay in interleaved mode
        updateTriggers: { getFillColor: [values] },
      });

    // Before any scenario only the left map shows, so it carries the pins.
    const leftPins = hasRight ? [] : [new ScatterplotLayer({
      id: "pins-l", data: p.pins, getPosition: (d: any) => [d.lon, d.lat], radiusUnits: "pixels", getRadius: 7,
      getFillColor: [250, 204, 21, 255], getLineColor: [14, 17, 22, 255], stroked: true,
      lineWidthUnits: "pixels", getLineWidth: 2,
    })];
    ol.setProps({
      layers: [hex("time-l", p.left?.time_min ?? null, timeColor), communes("l"), outline("l"),
        facilityLayer("fac-l", p.left?.facilities ?? []), ...leftPins],
    });

    const editsLines = new GeoJsonLayer({
      id: "edits",
      data: p.edits ?? { type: "FeatureCollection", features: [] },
      stroked: true,
      filled: true,
      pointType: "circle",
      lineWidthUnits: "pixels",
      getLineWidth: (f: any) => (f.properties.kind === "new_road" ? 4 : 3),
      getLineColor: (f: any) => [...(EDIT_COLORS as any)[f.properties.kind], 255] as [number, number, number, number],
      getFillColor: [14, 17, 22, 255],
      pointRadiusUnits: "pixels",
      getPointRadius: 9,
      getDashArray: (f: any) => (f.properties.kind === "closed_road" ? [3, 2] : [0, 0]),
      dashJustified: true,
      extensions: [new PathStyleExtension({ dash: true })],
      updateTriggers: { getLineColor: [p.edits] },
    });
    const editMarks = new TextLayer({
      id: "edit-marks",
      data: (p.edits?.features ?? []).filter((f) => f.geometry.type === "Point"),
      getPosition: (f: any) => f.geometry.coordinates,
      getText: (f: any) => (f.properties.kind === "new_facility" ? "+" : "×"),
      getColor: (f: any) => [...(EDIT_COLORS as any)[f.properties.kind], 255] as [number, number, number, number],
      getSize: 18,
      fontWeight: 700,
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
    });
    const pins = new ScatterplotLayer({
      id: "pins",
      data: p.pins,
      getPosition: (d: any) => [d.lon, d.lat],
      radiusUnits: "pixels",
      getRadius: 7,
      getFillColor: [250, 204, 21, 255],
      getLineColor: [14, 17, 22, 255],
      stroked: true,
      lineWidthUnits: "pixels",
      getLineWidth: 2,
    });
    const reach = new H3HexagonLayer<Cell>({
      id: "reach",
      data: cells,
      getHexagon: (d) => d.h,
      getFillColor: (d) => {
        const t = p.reach?.[d.i];
        if (t === null || t === undefined || t > p.reachT) return [0, 0, 0, 0];
        const edge = Math.max(0, 1 - (p.reachT - t) / 4); // the moving front glows
        return [...REACH_COLOR, Math.round(60 + 160 * edge)] as any;
      },
      extruded: false,
      stroked: false,
      coverage: 0.96,
      updateTriggers: { getFillColor: [p.reach, p.reachT] },
    });
    const main = p.view === "diff" && p.delta
      ? hex("delta-r", p.delta, deltaColor)
      : hex("time-r", p.right?.time_min ?? null, timeColor);
    or.setProps({
      layers: [main, communes("r"), outline("r"), ...(p.reach ? [reach] : []), facilityLayer("fac-r", p.right?.facilities ?? []),
        editsLines, editMarks, pins],
    });
  }, [p.left, p.right, p.delta, p.view, p.edits, p.reach, p.reachT, p.pins, cells, p.base, hasRight, labelId]);

  useImperativeHandle(ref, () => ({
    snapshot: () => {
      const l = maps.current.l?.getCanvas();
      const r = maps.current.r?.getCanvas();
      if (!l) return null;
      const out = document.createElement("canvas");
      out.width = l.width;
      out.height = l.height;
      const ctx = out.getContext("2d")!;
      if (!hasRight || !r) ctx.drawImage(l, 0, 0);
      else if (p.view === "diff") ctx.drawImage(r, 0, 0);
      else {
        ctx.drawImage(l, 0, 0);
        const x = (out.width * swipe) / 100;
        ctx.drawImage(r, x, 0, out.width - x, out.height, x, 0, out.width - x, out.height);
        ctx.fillStyle = "#e6e8eb";
        ctx.fillRect(x - 1, 0, 2, out.height);
      }
      return out;
    },
  }));

  const drag = (e: React.PointerEvent) => {
    const box = wrap.current!.getBoundingClientRect();
    const move = (ev: PointerEvent) =>
      setSwipe(Math.min(98, Math.max(2, ((ev.clientX - box.left) / box.width) * 100)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    e.preventDefault();
  };

  return (
    <div ref={wrap} className="relative h-full w-full overflow-hidden bg-[var(--bg)]">
      {/* inline position: maplibre's stylesheet sets .maplibregl-map to relative */}
      <div ref={leftEl} style={{ position: "absolute", inset: 0, visibility: hasRight && p.view === "diff" ? "hidden" : "visible" }} />
      <div
        ref={rightEl}
        style={{
          position: "absolute",
          inset: 0,
          visibility: hasRight ? "visible" : "hidden",
          clipPath: split ? `inset(0 0 0 ${swipe}%)` : "none",
        }}
      />
      {split && (
        <>
          <div className="pointer-events-none absolute inset-y-0 w-px bg-[var(--fg)]/80" style={{ left: `${swipe}%` }} />
          <button
            aria-label="Drag to compare the real city and the scenario"
            onPointerDown={drag}
            className="absolute top-1/2 z-10 grid h-9 w-9 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize place-items-center rounded-full border border-[var(--line)] bg-[var(--panel)] text-[var(--fg)] shadow-lg"
            style={{ left: `${swipe}%` }}
          >
            <span className="text-xs tracking-tighter">◀▶</span>
          </button>
          <span className="map-tag left-3">{p.leftLabel}</span>
          <span className="map-tag right-3">{p.rightLabel}</span>
        </>
      )}
      {hasRight && p.view === "diff" && <span className="map-tag left-3">Change: {p.rightLabel} vs real city</span>}
      {!hasRight && <span className="map-tag left-3">{p.leftLabel}</span>}
    </div>
  );
});

export default MapView;
