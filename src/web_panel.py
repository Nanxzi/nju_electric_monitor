"""Render a lightweight electricity dashboard from the collected CSV."""

import math
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, Response, render_template, send_file

app = Flask(__name__)
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "electricity_data.csv"


def local_time(value):
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return pd.NaT
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("Asia/Shanghai")
    return timestamp.tz_convert("Asia/Shanghai")


def prepare_dashboard(frame):
    """Normalize readings and estimate use without counting balance increases."""
    if not {"time", "num"}.issubset(frame.columns):
        raise ValueError("Electricity CSV must contain time and num columns")
    frame = frame.copy()
    frame["time"] = frame["time"].map(local_time)
    frame["num"] = pd.to_numeric(frame["num"], errors="coerce")
    frame = frame.dropna(subset=["time", "num"])
    frame = frame.loc[frame["num"].map(math.isfinite).astype(bool)]
    frame = frame.sort_values("time", kind="stable").drop_duplicates("time", keep="last")

    records = []
    previous = None
    for row in frame.itertuples(index=False):
        balance = float(row.num)
        records.append({
            "time": row.time.isoformat(),
            "display_time": row.time.strftime("%Y-%m-%d %H:%M:%S"),
            "balance": round(balance, 2),
            "change": round(balance - previous, 2) if previous is not None else None,
        })
        previous = balance

    average = None
    daily_usage = None
    estimated_days = None
    if len(frame) > 1:
        latest_timestamp = datetime.fromisoformat(records[-1]["time"]).timestamp()
        recent = [row for row in records if datetime.fromisoformat(row["time"]).timestamp() >= latest_timestamp - 7 * 86400]
        span = (latest_timestamp - datetime.fromisoformat(recent[0]["time"]).timestamp()) / 86400
        if span >= 1:
            used = sum(max(0, previous["balance"] - current["balance"]) for previous, current in zip(recent, recent[1:]))
            average = used / span
            daily_usage = average
            if average > 0:
                estimated_days = round(max(0, records[-1]["balance"]) / average, 1)
            average = round(average, 2)

    latest = records[-1] if records else None
    return {
        "records": records,
        "count": len(records),
        "balance": latest["balance"] if latest else None,
        "latest_change": latest["change"] if latest else None,
        "last_updated": latest["display_time"] if latest else None,
        "latest_iso": latest["time"] if latest else None,
        "average": average,
        "daily_usage": daily_usage,
        "estimated_days": estimated_days,
    }


@app.route("/")
def index():
    try:
        frame = pd.read_csv(CSV_PATH)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        frame = pd.DataFrame(columns=["time", "num"])
    dashboard = prepare_dashboard(frame)
    return render_template("dashboard.html", dashboard=dashboard)


@app.route("/electricity_data.csv")
def download_csv():
    if not CSV_PATH.is_file() or CSV_PATH.stat().st_size == 0:
        return Response("time,num,unit\n", mimetype="text/csv", headers={
            "Content-Disposition": "attachment; filename=electricity_data.csv",
        })
    return send_file(CSV_PATH, as_attachment=True, download_name="electricity_data.csv")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
