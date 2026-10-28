"""Charts as Vega-Lite specs built in code. The model never writes chart code; it only chooses what to
investigate, and the chart is built from the same numbers the answer uses."""
from __future__ import annotations

EFFECT_LABELS = {"volume": "Usage (volume)", "mix": "Mix (pricier items)", "rate": "Price (rate)",
                 "credits": "Credits", "one_off_purchases": "One-off purchases", "other_charges": "Other"}


def daily_spend(series: list[dict], title: str, change_start: str | None = None, events: list[dict] | None = None,
                period: list[str] | None = None) -> dict:
    layers = []
    if period:
        layers.append({"data": {"values": [{"start": period[0], "end": period[1]}]},
                       "mark": {"type": "rect", "opacity": 0.08, "color": "#4c78a8"},
                       "encoding": {"x": {"field": "start", "type": "temporal"}, "x2": {"field": "end"}}})
    layers.append({"data": {"values": series}, "mark": {"type": "line", "point": False, "strokeWidth": 1.6},
                   "encoding": {"x": {"field": "period", "type": "temporal", "title": None},
                                "y": {"field": "value", "type": "quantitative", "title": "Effective cost (USD/day)"},
                                "tooltip": [{"field": "period", "type": "temporal"},
                                            {"field": "value", "type": "quantitative", "format": ",.0f"}]}})
    if change_start:
        layers.append({"data": {"values": [{"day": change_start, "label": "change starts"}]},
                       "mark": {"type": "rule", "color": "#e45756", "strokeDash": [4, 3]},
                       "encoding": {"x": {"field": "day", "type": "temporal"}, "tooltip": [{"field": "label"}]}})
    if events:
        layers.append({"data": {"values": [{"day": e["event_date"], "label": e["description"][:60]} for e in events]},
                       "mark": {"type": "point", "shape": "triangle-down", "size": 90, "filled": True,
                                "color": "#f58518"},
                       "encoding": {"x": {"field": "day", "type": "temporal"}, "y": {"value": 6},
                                    "tooltip": [{"field": "day", "type": "temporal"}, {"field": "label"}]}})
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "title": title, "width": "container",
            "height": 260, "layer": layers}


def effects_bar(effects: dict) -> dict:
    vals = [{"effect": EFFECT_LABELS.get(k, k), "usd": v} for k, v in effects.items() if v and abs(v) >= 1]
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "title": "What made up the change",
            "width": "container", "height": 40 + 28 * max(len(vals), 1), "data": {"values": vals},
            "mark": {"type": "bar"},
            "encoding": {"y": {"field": "effect", "type": "nominal", "sort": "-x", "title": None},
                         "x": {"field": "usd", "type": "quantitative", "title": "Change (USD)"},
                         "color": {"condition": {"test": "datum.usd < 0", "value": "#54a24b"}, "value": "#e45756"},
                         "tooltip": [{"field": "effect"}, {"field": "usd", "format": ",.0f"}]}}


def bar_from_breakdown(b: dict, title: str) -> dict:
    vals = [{"segment": s["segment"], "change": s["change"]} for s in b.get("segments", [])[:8]]
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "title": title, "width": "container",
            "height": 40 + 28 * max(len(vals), 1), "data": {"values": vals}, "mark": "bar",
            "encoding": {"y": {"field": "segment", "type": "nominal", "sort": "-x", "title": None},
                         "x": {"field": "change", "type": "quantitative", "title": "Change vs baseline (USD)"},
                         "tooltip": [{"field": "segment"}, {"field": "change", "format": ",.0f"}]}}
