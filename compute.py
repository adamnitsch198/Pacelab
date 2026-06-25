"""Aerobic-decoupling computation from activity streams."""


def decoupling_from_streams(hr, vel, dist, label="long run", n_points=46):
    """
    Pa:HR decoupling — compares speed/HR efficiency in the first vs second half
    of the moving portion of a run. Returns the drift % plus a downsampled
    HR/pace series for charting.
    """
    pts = [(dist[i], hr[i], vel[i]) for i in range(min(len(hr), len(vel), len(dist)))
           if vel[i] > 1.8 and hr[i] and hr[i] > 100]
    if len(pts) < 10:
        return None

    half = len(pts) // 2

    def ef(seg):
        s = sum(p[2] for p in seg) / len(seg)
        h = sum(p[1] for p in seg) / len(seg)
        return s / h

    ef1, ef2 = ef(pts[:half]), ef(pts[half:])
    decoup = (ef1 - ef2) / ef1 * 100
    avg_hr1 = sum(p[1] for p in pts[:half]) / half
    avg_hr2 = sum(p[1] for p in pts[half:]) / (len(pts) - half)

    series = []
    step = len(pts) / n_points
    for k in range(n_points):
        seg = pts[int(k * step):int((k + 1) * step)] or [pts[min(int(k * step), len(pts) - 1)]]
        d = sum(p[0] for p in seg) / len(seg) / 1609.34
        h = sum(p[1] for p in seg) / len(seg)
        v = sum(p[2] for p in seg) / len(seg)
        pace = 1609.34 / (v * 60) if v > 0 else None
        series.append({"mi": round(d, 2), "hr": round(h), "pace": round(pace, 2)})

    return {
        "activity": label,
        "pct": round(decoup, 1),
        "firstHalfHr": round(avg_hr1),
        "secondHalfHr": round(avg_hr2),
        "series": series,
    }
