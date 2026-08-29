"""
Roast Coach's charts.

Colours come from a validated categorical palette (checked for colour-vision
separation and contrast in both light and dark surfaces). Series that share a
panel also carry a second encoding -- dash pattern plus a direct end-of-line
label -- so identity is never colour alone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- palette ---------------------------------------------------------------

PALETTE = {
    "light": {
        "surface": "#fcfcfb",
        "text": "#0b0b0b",
        "muted": "#52514e",
        "grid": "#e6e5e1",
        "bean": "#2a78d6",   # blue
        "drum": "#eb6834",   # orange
        "power": "#4a3aa7",  # violet
        "fan": "#008300",    # green
        "drumctl": "#e34948",  # red
        "aqua": "#1baf7a",
        "event": "#8a8880",
        "template": "plotly_white",
    },
    "dark": {
        "surface": "#1a1a19",
        "text": "#ffffff",
        "muted": "#c3c2b7",
        "grid": "#38383a",
        "bean": "#3987e5",
        "drum": "#d95926",
        "power": "#9085e9",
        "fan": "#008300",
        "drumctl": "#e66767",
        "aqua": "#199e70",
        "event": "#8a8880",
        "template": "plotly_dark",
    },
}


def colors(theme: str) -> dict:
    return PALETTE.get(theme, PALETTE["light"])


def _style(fig: go.Figure, c: dict, height: int) -> go.Figure:
    fig.update_layout(
        template=c["template"],
        height=height,
        paper_bgcolor=c["surface"],
        plot_bgcolor=c["surface"],
        font=dict(color=c["text"], size=13),
        margin=dict(l=80, r=124, t=70, b=70),
        legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="left", x=0),
    )
    # A shared crosshair suits the stacked single-roast panels; anything that set
    # its own hover mode (scatter, overlays of many roasts) keeps it.
    if fig.layout.hovermode is None:
        fig.update_layout(hovermode="x unified")
    fig.update_annotations(selector=dict(font_size=16), font_size=13)
    fig.update_xaxes(gridcolor=c["grid"], zeroline=False, linecolor=c["grid"])
    fig.update_yaxes(gridcolor=c["grid"], zeroline=False, linecolor=c["grid"])
    return fig


def _end_label(fig: go.Figure, x, y, text: str, color: str, row: int, muted: str, yshift: int = 0) -> None:
    """Selective direct label at the right end of a line."""
    if x is None or y is None or (isinstance(y, float) and np.isnan(y)):
        return
    fig.add_annotation(
        x=x, y=y, text=text, row=row, col=1,
        xanchor="left", xshift=6, yshift=yshift, showarrow=False,
        font=dict(size=11, color=muted), bgcolor="rgba(0,0,0,0)",
    )


def turning_point_row(samples: pd.DataFrame) -> int:
    """Row of the turning point -- where the bean stops cooling and starts roasting."""
    column = "smoothBeanTemperature" if "smoothBeanTemperature" in samples else "beanTemperature"
    if column not in samples or samples[column].dropna().empty:
        return 0
    window = samples[column].iloc[: max(3, int(len(samples) * 0.4))]
    if window.dropna().empty:
        return 0
    return int(window.idxmin())


def ror_view_range(samples: pd.DataFrame) -> tuple[float, float] | None:
    """A readable y-range for rate of rise.

    Charge throws both sensors into a transient an order of magnitude larger than
    anything that follows -- the probe can read -250 °C/min as cold beans hit it --
    which flattens the 0-20 °C/min band roasters actually read. The range is taken
    from the samples after the turning point instead. Nothing is dropped: the
    curves still run off the top of the panel, and the chart's autoscale button
    restores the full extent.
    """
    columns = [c for c in ("smoothBeanDerivative", "smoothDrumDerivative") if c in samples]
    if not columns:
        return None
    after = samples.loc[turning_point_row(samples):, columns]
    values = pd.concat([after[c] for c in columns]).dropna()
    if len(values) < 5:
        return None
    low, high = float(values.min()), float(values.max())
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return None
    pad = (high - low) * 0.08
    return min(0.0, low - pad), high + pad


def _last_valid(samples: pd.DataFrame, column: str):
    if column not in samples:
        return None, None
    s = samples[column].dropna()
    if s.empty:
        return None, None
    return samples.loc[s.index[-1], "time_minutes"], s.iloc[-1]


# --- roast profile ---------------------------------------------------------

def roast_profile_figure(
    samples: pd.DataFrame,
    events: list[tuple[str, float]],
    theme: str = "light",
    title: str = "Roast profile",
    show_raw: bool = False,
    show_second_derivative: bool = False,
) -> go.Figure:
    """Temperature, rate of rise, controls -- and optionally the second derivative
    -- as stacked panels on one shared time axis.

    Deliberately stacked panels rather than the classic twin-axis roast graph:
    two different measures on two y-scales against one x makes the crossing point
    of the curves meaningless.
    """
    c = colors(theme)
    heights = [0.45, 0.3, 0.25] if not show_second_derivative else [0.36, 0.24, 0.2, 0.2]
    rows = len(heights)
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.055,
                        row_heights=heights)

    t = samples["time_minutes"]

    # Panel 1 -- temperatures
    for i, (column, name, color, dash) in enumerate((
        ("smoothDrumTemperature", "IBTS", c["drum"], "solid"),
        ("smoothBeanTemperature", "Bean probe", c["bean"], "dot"),
    )):
        if column not in samples:
            continue
        fig.add_trace(
            go.Scatter(
                x=t, y=samples[column], name=name, legendgroup=name,
                mode="lines", line=dict(color=color, width=2, dash=dash),
                hovertemplate="%{y:.1f} °C<extra>" + name + "</extra>",
            ),
            row=1, col=1,
        )
        lx, ly = _last_valid(samples, column)
        _end_label(fig, lx, ly, name, color, 1, c["muted"], yshift=9 if i == 0 else -9)

    if show_raw:
        for column, name, color in (
            ("drumTemperature", "IBTS raw", c["drum"]),
            ("beanTemperature", "Bean probe raw", c["bean"]),
        ):
            if column in samples:
                fig.add_trace(
                    go.Scatter(
                        x=t, y=samples[column], name=name, mode="lines",
                        line=dict(color=color, width=1), opacity=0.25,
                        showlegend=False, hoverinfo="skip",
                    ),
                    row=1, col=1,
                )

    # Panel 2 -- rate of rise
    for i, (column, name, color, dash) in enumerate((
        ("smoothDrumDerivative", "IBTS RoR", c["drum"], "solid"),
        ("smoothBeanDerivative", "Bean probe RoR", c["bean"], "dot"),
    )):
        if column not in samples:
            continue
        fig.add_trace(
            go.Scatter(
                x=t, y=samples[column], name=name, legendgroup=name,
                mode="lines", line=dict(color=color, width=2, dash=dash),
                showlegend=False,
                hovertemplate="%{y:.1f} °C/min<extra>" + name + "</extra>",
            ),
            row=2, col=1,
        )
        lx, ly = _last_valid(samples, column)
        _end_label(fig, lx, ly, name, color, 2, c["muted"], yshift=9 if i == 0 else -9)

    # Optional panel -- second derivative of the IBTS rate of rise
    second_row = 3 if show_second_derivative else None
    if second_row and "secondDerivative" in samples:
        fig.add_trace(
            go.Scatter(
                x=t, y=samples["secondDerivative"], name="IBTS 2nd derivative",
                mode="lines", line=dict(color=c["power"], width=2), showlegend=False,
                hovertemplate="%{y:.2f} °C/min²<extra>2nd derivative</extra>",
            ),
            row=second_row, col=1,
        )
        fig.add_hline(y=0, line=dict(color=c["grid"], width=1), row=second_row, col=1)
        lx, ly = _last_valid(samples, "secondDerivative")
        _end_label(fig, lx, ly, "2nd deriv.", c["power"], second_row, c["muted"])

    # Last panel -- control settings (step lines: a setting holds until changed)
    control_row = rows
    control_labels = []
    for column, name, color, dash in (
        ("power", "Power", c["power"], "solid"),
        ("fan", "Fan", c["fan"], "dash"),
        ("drum", "Drum", c["drumctl"], "dot"),
    ):
        if column not in samples or samples[column].dropna().empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=t, y=samples[column], name=name, mode="lines",
                line=dict(color=color, width=2, dash=dash, shape="hv"),
                hovertemplate="%{y:.0f}<extra>" + name + "</extra>",
            ),
            row=control_row, col=1,
        )
        lx, ly = _last_valid(samples, column)
        if lx is not None:
            control_labels.append((ly, lx, name, color))

    # Controls often end on the same value; space the end labels by rank so
    # they cannot land on top of each other.
    control_labels.sort(key=lambda item: item[0])
    spread = [0] if len(control_labels) == 1 else ([-9, 9] if len(control_labels) == 2 else [-14, 0, 14])
    for shift, (ly, lx, name, color) in zip(spread, control_labels):
        _end_label(fig, lx, ly, name, color, control_row, c["muted"], yshift=shift)

    # Event markers across every panel
    for label, seconds in events:
        minutes = seconds / 60.0
        fig.add_vline(x=minutes, line=dict(color=c["event"], width=1, dash="dot"), row="all", col=1)
        fig.add_annotation(
            x=minutes, y=1.0, yref="y domain", row=1, col=1,
            text=label, showarrow=False, yanchor="bottom",
            font=dict(size=10, color=c["muted"]),
        )

    fig.update_yaxes(title_text="temperature (°C)", row=1, col=1)
    fig.update_yaxes(title_text="rate of rise (°C/min)", row=2, col=1)
    if second_row:
        fig.update_yaxes(title_text="2nd derivative", row=second_row, col=1)
    fig.update_yaxes(title_text="control setting", row=control_row, col=1)
    fig.update_xaxes(title_text="time (minutes)", row=control_row, col=1)
    fig.update_layout(title=title)

    view = ror_view_range(samples)
    clipped = False
    if view is not None:
        rates = pd.concat(
            [samples[c] for c in ("smoothBeanDerivative", "smoothDrumDerivative") if c in samples]
        ).dropna()
        clipped = bool(len(rates) and (rates.min() < view[0] or rates.max() > view[1]))
        fig.update_yaxes(range=list(view), row=2, col=1)
    fig.update_layout(meta={"ror_clipped": clipped})

    return _style(fig, c, 900 if show_second_derivative else 760)


# --- comparison across roasts ----------------------------------------------

# Single-hue ramp for colouring many lines by a continuous value (light -> dark).
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

# Diverging pair for correlations: two poles that read as opposite, neutral middle.
DIVERGING_LIGHT = [[0.0, "#0d366b"], [0.25, "#6da7ec"], [0.5, "#f0efec"], [0.75, "#e87d7c"], [1.0, "#a81f1e"]]
DIVERGING_DARK = [[0.0, "#184f95"], [0.25, "#3987e5"], [0.5, "#383835"], [0.75, "#e66767"], [1.0, "#c02f2e"]]


def all_roasts_figure(
    curves: dict,
    theme: str = "light",
    measure: str = "smoothDrumTemperature",
    highlight: str | None = None,
    color_values: dict | None = None,
    color_label: str = "",
) -> go.Figure:
    """Every selected roast's curve on one axis.

    With more than a handful of roasts, identity by colour stops working, so the
    default is one recessive colour for the whole set with the selected roast
    picked out. Colouring by a measured value instead uses a single-hue ramp,
    which is a magnitude encoding rather than an identity one.
    """
    c = colors(theme)
    fig = go.Figure()

    labels = {
        "smoothDrumTemperature": "IBTS temperature (°C)",
        "smoothBeanTemperature": "Bean probe temperature (°C)",
        "smoothDrumDerivative": "IBTS rate of rise (°C/min)",
        "smoothBeanDerivative": "Bean rate of rise (°C/min)",
        "secondDerivative": "IBTS second derivative (°C/min²)",
    }

    scale = None
    if color_values:
        numbers = [v for v in color_values.values() if v is not None and np.isfinite(v)]
        if numbers:
            low, high = min(numbers), max(numbers)
            scale = (low, high if high > low else low + 1)

    def line_color(key: str) -> str:
        if scale and key in color_values and color_values[key] is not None and np.isfinite(color_values[key]):
            position = (color_values[key] - scale[0]) / (scale[1] - scale[0])
            return SEQUENTIAL[min(len(SEQUENTIAL) - 1, max(0, int(position * (len(SEQUENTIAL) - 1))))]
        return c["muted"]

    for key, (name, frame) in curves.items():
        if measure not in frame:
            continue
        is_highlight = key == highlight
        fig.add_trace(
            go.Scatter(
                x=frame["time_minutes"], y=frame[measure], name=name,
                mode="lines", showlegend=False,
                line=dict(
                    color=c["drum"] if is_highlight else line_color(key),
                    width=2.5 if is_highlight else 1.2,
                ),
                opacity=1.0 if is_highlight else (0.85 if scale else 0.45),
                hovertemplate="%{y:.1f}<br>%{x:.1f} min<extra>" + name + "</extra>",
            )
        )

    if scale:
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None], mode="markers", showlegend=False, hoverinfo="skip",
                marker=dict(
                    colorscale=[[i / (len(SEQUENTIAL) - 1), s] for i, s in enumerate(SEQUENTIAL)],
                    cmin=scale[0], cmax=scale[1], showscale=True,
                    colorbar=dict(title=dict(text=color_label, side="right"), thickness=12, len=0.7),
                ),
            )
        )

    fig.update_xaxes(title_text="time (minutes)")
    fig.update_yaxes(title_text=labels.get(measure, measure))

    # Rate-of-rise curves carry a charge transient an order of magnitude larger
    # than the rest; frame the axis on what happens after the turning point.
    if "Derivative" in measure or measure == "secondDerivative":
        spans = [ror_view_range(frame) for _, frame in curves.values()]
        spans = [span for span in spans if span]
        if spans:
            fig.update_yaxes(range=[min(s[0] for s in spans), max(s[1] for s in spans)])

    title = f"{len(curves)} roasts · {labels.get(measure, measure)}"
    fig.update_layout(title=title, hovermode="closest")
    return _style(fig, c, 620)



# --- comparing several roasts ----------------------------------------------

# One colour per roast, in a fixed order — never cycled, never reassigned when
# the selection changes, so a roast keeps its colour as others come and go.
#
# Three, and not more, because overlaid curves cross: every pair is adjacent
# somewhere on the chart, and under that all-pairs test three is as far as this
# palette gets while staying separable for colour-blind readers in both light
# and dark. (Checked, not judged: blue/orange/aqua clear the floors in both
# modes; every fourth hue tried fails one of them in dark.) A fourth roast is
# not drawn in a colour nobody can distinguish — it gets its own panel instead.
SERIES = {
    "light": ("#2a78d6", "#eb6834", "#1baf7a"),
    "dark": ("#3987e5", "#d95926", "#199e70"),
}

# The second encoding, so identity never rests on colour alone.
DASHES = ("solid", "dash", "dot")

OVERLAY_LIMIT = len(DASHES)

# The IBTS, smoothed — the same series the single-roast profile draws, so a roast
# looks the same wherever it appears. RoasTime files the IBTS under
# `drumTemperature`; it is the infrared sensor reading the beans, not the drum.
_TEMPERATURE = "smoothDrumTemperature"
_RATE = "smoothDrumDerivative"


def _minutes(curve):
    """Minutes from charge, whichever way this frame spells time."""
    if "time_minutes" in curve:
        return curve["time_minutes"]
    if "seconds" in curve:
        return curve["seconds"] / 60.0
    return curve["time_seconds"] / 60.0


def series_colors(theme: str) -> tuple:
    return SERIES.get(theme, SERIES["light"])


def _mark_event(fig: go.Figure, x, y, label: str, color: str, row: int, surface: str) -> None:
    """A first crack or a drop, as a ringed point on the curve it belongs to."""
    if x is None or y is None:
        return
    fig.add_trace(
        go.Scatter(
            x=[x], y=[y], mode="markers", showlegend=False,
            marker=dict(size=9, color=color, line=dict(color=surface, width=2)),
            hovertemplate=f"{label}: %{{x:.1f}} min, %{{y:.1f}} °C<extra></extra>",
        ),
        row=row, col=1,
    )


def compare_figure(roasts: list, theme: str = "light", align: str = "charge",
                   height: int = 620) -> go.Figure:
    """Several roasts on one pair of axes: IBTS temperature, and its rate of rise.

    ``roasts`` is ``[{"label", "curve", "first_crack", "drop"}, …]`` — the curve
    being the sample frame the profile chart already draws from, and the two
    moments in minutes.

    The IBTS is the line, because it is the line the roaster watches and the one
    the recipes are written against. Both panels share a time axis and each has
    its own scale, which is the whole reason they are stacked rather than drawn
    on twin axes: temperature and rate of rise share no units, and crossing them
    on one plot would invite a comparison that means nothing.

    With ``align="first crack"`` time is measured from first crack instead of
    from charge, so development is compared like with like — three roasts that
    cracked at 8.2, 9.4 and 10.1 minutes line up on the moment that matters.
    """
    c = colors(theme)
    hues = series_colors(theme)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                        row_heights=[0.58, 0.42])

    drawn = 0
    for position, roast in enumerate(roasts[:OVERLAY_LIMIT]):
        curve = roast.get("curve")
        if curve is None or getattr(curve, "empty", True):
            continue
        colour = hues[position % len(hues)]
        dash = DASHES[position % len(DASHES)]
        label = str(roast.get("label") or f"roast {position + 1}")

        shift = float(roast.get("first_crack") or 0.0) if align == "first crack" else 0.0
        minutes = _minutes(curve) - shift

        for row, column, unit in ((1, _TEMPERATURE, "°C"), (2, _RATE, "°C/min")):
            if column not in curve:
                continue
            fig.add_trace(
                go.Scatter(
                    x=minutes, y=curve[column], name=label, legendgroup=label,
                    mode="lines", line=dict(color=colour, width=2, dash=dash),
                    showlegend=(row == 1),
                    hovertemplate="%{y:.1f} " + unit + " at %{x:.1f} min"
                                  f"<extra>{label}</extra>",
                ),
                row=row, col=1,
            )

        # Direct labels as well as the legend: four series or fewer are named on
        # the chart itself, so identity never rests on colour alone.
        ends = (curve[_TEMPERATURE].dropna() if _TEMPERATURE in curve
                else pd.Series(dtype=float))
        if not ends.empty:
            # Three roasts of one coffee end within a degree or two of each
            # other, so the labels would sit on top of one another. Fan them out.
            _end_label(fig, minutes.loc[ends.index[-1]], ends.iloc[-1], label,
                       colour, 1, c["muted"], yshift=(1 - position) * 18)

        # Only first crack is marked. The drop is where the line stops — a dot
        # on the end of a line that has already ended says nothing, and it lands
        # exactly where the roast's name has to go.
        for moment, mark in (("first_crack", "first crack"),):
            when = roast.get(moment)
            if when is None or not np.isfinite(float(when)):
                continue
            at = float(when) - shift
            nearest = (minutes - at).abs().idxmin() if len(minutes) else None
            if nearest is not None and _TEMPERATURE in curve:
                _mark_event(fig, at, curve.loc[nearest, _TEMPERATURE],
                            f"{label} · {mark}", colour, 1, c["surface"])
        drawn += 1

    # Charge throws the rate of rise to −200 °C/min for a few seconds, which
    # flattens the 0–20 band the roast is actually read in. Scale to what is
    # readable; the autoscale button still restores the whole extent.
    windows = [ror_view_range(roast["curve"]) for roast in roasts[:OVERLAY_LIMIT]
               if roast.get("curve") is not None and not roast["curve"].empty]
    windows = [window for window in windows if window]
    if windows:
        fig.update_yaxes(range=[min(low for low, _ in windows),
                                max(high for _, high in windows)], row=2, col=1)

    zero = "first crack" if align == "first crack" else "charge"
    fig.update_yaxes(title_text="IBTS °C", row=1, col=1, gridcolor=c["grid"])
    fig.update_yaxes(title_text="IBTS rate of rise °C/min", row=2, col=1,
                     gridcolor=c["grid"])
    fig.update_xaxes(title_text=f"minutes from {zero}", row=2, col=1, gridcolor=c["grid"])
    fig.update_xaxes(gridcolor=c["grid"], row=1, col=1)
    if align == "first crack":
        for row in (1, 2):
            fig.add_vline(x=0, line=dict(color=c["event"], width=1, dash="dot"),
                          row=row, col=1)
    fig.update_layout(hovermode="x unified",
                      title=dict(text=f"IBTS · {drawn} roasts, lined up at {zero}"))
    return _style(fig, c, height)


def small_multiples_figure(roasts: list, theme: str = "light", height: int = 0) -> go.Figure:
    """One panel per roast, same axes throughout, with the first as a ghost behind.

    Past three roasts, an overlay stops being a comparison and becomes a knot.
    The honest form is one panel each on identical scales: the eye compares
    shapes across panels perfectly well, and the roast picked first is drawn
    faintly on every one of them so there is always something to compare *to*.
    """
    c = colors(theme)
    usable = [roast for roast in roasts
              if roast.get("curve") is not None and not roast["curve"].empty]
    if not usable:
        return _style(go.Figure(), c, 260)

    reference = usable[0]
    fig = make_subplots(rows=len(usable), cols=1, shared_xaxes=True,
                        vertical_spacing=0.035,
                        subplot_titles=[str(roast.get("label") or "") for roast in usable])

    for position, roast in enumerate(usable, start=1):
        curve = roast["curve"]
        minutes = _minutes(curve)

        if position > 1:
            ghost = reference["curve"]
            fig.add_trace(
                go.Scatter(x=_minutes(ghost), y=ghost[_TEMPERATURE],
                           mode="lines", line=dict(color=c["muted"], width=1),
                           opacity=0.35, showlegend=False,
                           hovertemplate="%{y:.1f} °C<extra>"
                                         f"{reference.get('label')}</extra>"),
                row=position, col=1)

        fig.add_trace(
            go.Scatter(x=minutes, y=curve[_TEMPERATURE], mode="lines",
                       line=dict(color=series_colors(theme)[0], width=2),
                       showlegend=False,
                       hovertemplate="%{y:.1f} °C at %{x:.1f} min<extra>"
                                     f"{roast.get('label')}</extra>"),
            row=position, col=1)

        for moment in ("first_crack",):
            when = roast.get(moment)
            if when is None or not np.isfinite(float(when)):
                continue
            nearest = (minutes - float(when)).abs().idxmin() if len(minutes) else None
            if nearest is not None:
                _mark_event(fig, float(when), curve.loc[nearest, _TEMPERATURE],
                            moment.replace("_", " "), series_colors(theme)[0],
                            position, c["surface"])

    fig.update_xaxes(title_text="minutes from charge", row=len(usable), col=1,
                     gridcolor=c["grid"])
    fig.update_yaxes(title_text="IBTS °C", gridcolor=c["grid"])
    for note in fig.layout.annotations:
        note.font = dict(size=12, color=c["muted"])
    fig.update_layout(hovermode="x")
    return _style(fig, c, height or max(260, 150 * len(usable) + 90))


def trend_figure(same_coffee: pd.DataFrame, theme: str = "light") -> go.Figure:
    """How one coffee has moved, roast by roast.

    Three measures on three panels rather than one crowded axis: minutes and
    degrees do not belong on the same scale, and the shape of each line is the
    point.
    """
    c = colors(theme)
    data = same_coffee.dropna(subset=["roasted_at"]).sort_values("roasted_at")
    panels = [
        ("firstCrackTime", "first crack (min)", c["bean"]),
        ("developmentMinutes", "development (min)", c["drum"]),
        ("drumDropTemperature", "drop temperature (°C)", c["aqua"]),
    ]
    data = data.copy()
    data["developmentMinutes"] = data["totalRoastMinutes"] - data["firstCrackTime"]

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.09)
    for position, (column, label, color) in enumerate(panels, start=1):
        if column not in data:
            continue
        series = data[column]
        fig.add_trace(
            go.Scatter(
                x=data["roasted_at"], y=series, mode="lines+markers", name=label,
                line=dict(color=color, width=2), marker=dict(size=8),
                showlegend=False,
                customdata=data["label"],
                hovertemplate="%{customdata}<br>%{y:.1f}<extra>" + label + "</extra>",
            ),
            row=position, col=1,
        )
        if series.notna().sum() > 1:
            fig.add_hline(y=float(series.mean()), line=dict(color=c["grid"], width=1, dash="dot"),
                          row=position, col=1)
            # A measure that barely moves should look steady, not magnified.
            low, high = float(series.min()), float(series.max())
            pad = max((high - low) * 0.25, abs(series.mean()) * 0.02, 0.3)
            fig.update_yaxes(range=[low - pad, high + pad], row=position, col=1)
        fig.update_yaxes(title_text=label, row=position, col=1)

    reference = data[data.get("is_reference", 0) == 1]
    for _, row in reference.iterrows():
        fig.add_vline(x=row["roasted_at"], line=dict(color=c["event"], width=1, dash="dash"),
                      row="all", col=1)

    fig.update_xaxes(title_text="roast date", row=3, col=1)
    fig.update_layout(title=f"{len(data)} roasts · dotted line is the average, "
                            "dashed line is your reference roast")
    return _style(fig, c, 620)


def effects_figure(learned: pd.DataFrame, theme: str = "light") -> go.Figure:
    """Where the coach started, and where your roasting has moved it.

    A dumbbell per effect: the textbook assumption, the value measured from your
    own roasts, and the distance between them.
    """
    c = colors(theme)
    data = learned.dropna(subset=["measured"]).copy()
    if data.empty:
        return _style(go.Figure(), c, 240)

    data["label"] = data["description"] + "  (" + data["units"] + ")"
    fig = go.Figure()

    for _, row in data.iterrows():
        fig.add_trace(go.Scatter(
            x=[row["prior"], row["measured"]], y=[row["label"], row["label"]],
            mode="lines", line=dict(color=c["grid"], width=3),
            showlegend=False, hoverinfo="skip"))

    fig.add_trace(go.Scatter(
        x=data["prior"], y=data["label"], mode="markers", name="starting assumption",
        marker=dict(color=c["bean"], size=11, line=dict(width=2, color=c["surface"])),
        hovertemplate="%{x:.2f}<extra>starting assumption</extra>"))
    fig.add_trace(go.Scatter(
        x=data["measured"], y=data["label"], mode="markers", name="measured on your machine",
        marker=dict(color=c["drum"], size=13, line=dict(width=2, color=c["surface"])),
        customdata=data["observations"],
        hovertemplate="%{x:.2f} · from %{customdata} roast pairs<extra>your machine</extra>"))

    fig.add_vline(x=0, line=dict(color=c["grid"], width=1))
    fig.update_xaxes(title_text="change per one step of the control")
    fig.update_layout(title="Effect sizes: assumed against measured", hovermode="closest")
    return _style(fig, c, max(280, 62 * len(data) + 150))
