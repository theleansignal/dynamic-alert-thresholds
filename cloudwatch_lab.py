"""
The Lean Signal: CloudWatch anomaly detection lab.

Pushes the same simulated traffic used in simulate.py to CloudWatch as a custom
metric, turns on anomaly detection, and creates an anomaly-band alarm.

Steps (run in order):
  python3 cloudwatch_lab.py backfill   # ~14 days of normal history, ending now
  python3 cloudwatch_lab.py detector   # turn on anomaly detection for the metric
  python3 cloudwatch_lab.py status     # check training: wait for TRAINED
  python3 cloudwatch_lab.py alarm      # create the anomaly-band alarm
  python3 cloudwatch_lab.py live       # stream new points every 5 min (Ctrl+C to stop)
  python3 cloudwatch_lab.py live --incident-after 30 --drop 0.15
                                       # normal for 30 min, then traffic falls to 15%
  python3 cloudwatch_lab.py cleanup    # delete the alarm and detector when done

Needs: pip install boto3 numpy, and `aws configure` done with lab-user's keys.
Cost: one custom metric + one anomaly alarm, well under $1 for a few days.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import boto3

NAMESPACE = "TheLeanSignal/Lab"
METRIC = "RequestsPerMinuteV2"
ALARM = "lean-signal-traffic-anomaly"
LOCAL_TZ = ZoneInfo("America/Chicago")   # the daily pattern follows this clock
PERIOD = 300                             # 5-minute data points
BAND_WIDTH = 2                           # CloudWatch's "K" (anomaly detection threshold)
HISTORY = timedelta(days=13, hours=20)   # PutMetricData accepts up to 2 weeks back

cw = boto3.client("cloudwatch", region_name=boto3.session.Session().region_name or "us-east-1")
rng = np.random.default_rng()


def expected_rpm(ts_utc):
    """Same shape as simulate.py: daily wave, lighter weekends, 2 AM batch job."""
    t = ts_utc.astimezone(LOCAL_TZ)
    hour = t.hour + t.minute / 60
    rpm = 450 + 350 * np.sin((hour - 9) / 24 * 2 * np.pi)
    if t.weekday() >= 5:
        rpm *= 0.6
    if 2 <= hour < 2.75:
        rpm += 900
    return rpm


def noisy(ts_utc, factor=1.0):
    return float(max(expected_rpm(ts_utc) * rng.normal(1.0, 0.05) * factor, 0))


def put(points):
    """points: list of (timestamp, value); sent in batches."""
    for i in range(0, len(points), 150):
        batch = points[i:i + 150]
        cw.put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{"MetricName": METRIC, "Timestamp": ts, "Value": v, "Unit": "Count"}
                        for ts, v in batch],
        )


def floor_5min(ts):
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


def cmd_backfill(_):
    end = floor_5min(datetime.now(timezone.utc))
    ts = floor_5min(end - HISTORY)
    points = []
    while ts <= end:
        points.append((ts, noisy(ts)))
        ts += timedelta(seconds=PERIOD)
    print(f"Sending {len(points)} points from {points[0][0]:%b %d %H:%M} to "
          f"{points[-1][0]:%b %d %H:%M} UTC ...")
    put(points)
    print("Backfill done. Next: python3 cloudwatch_lab.py detector")


def detector_spec():
    return {"Namespace": NAMESPACE, "MetricName": METRIC, "Dimensions": [], "Stat": "Average"}


def cmd_detector(_):
    cw.put_anomaly_detector(
        SingleMetricAnomalyDetector=detector_spec(),
        Configuration={"MetricTimezone": "America/Chicago"},
    )
    print("Anomaly detection enabled. Training can take hours or more.")
    print("Check with: python3 cloudwatch_lab.py status")


def cmd_status(_):
    res = cw.describe_anomaly_detectors(Namespace=NAMESPACE, MetricName=METRIC)
    dets = res.get("AnomalyDetectors", [])
    if not dets:
        print("No detector yet. Run: python3 cloudwatch_lab.py detector")
        return
    for d in dets:
        print("Detector state:", d.get("StateValue", "unknown"))
    print("Wait for TRAINED before recording the band.")


def cmd_alarm(_):
    cw.put_metric_alarm(
        AlarmName=ALARM,
        AlarmDescription="Traffic outside its learned normal band (The Lean Signal lab)",
        Metrics=[
            {"Id": "m1", "ReturnData": True,
             "MetricStat": {"Metric": {"Namespace": NAMESPACE, "MetricName": METRIC},
                            "Period": PERIOD, "Stat": "Average"}},
            {"Id": "ad1", "ReturnData": True, "Label": "expected band",
             "Expression": f"ANOMALY_DETECTION_BAND(m1, {BAND_WIDTH})"},
        ],
        ThresholdMetricId="ad1",
        ComparisonOperator="LessThanLowerOrGreaterThanUpperThreshold",
        EvaluationPeriods=2,
        DatapointsToAlarm=2,        # same debounce idea as MIN_CONSECUTIVE
        TreatMissingData="missing",
    )
    print(f"Alarm '{ALARM}' created (band width {BAND_WIDTH}).")


def cmd_live(args):
    start = time.time()
    print("Streaming one point every 5 minutes. Ctrl+C to stop.")
    if args.incident_after is not None:
        print(f"Traffic drops to {args.drop:.0%} of normal after {args.incident_after} minutes.")
    try:
        while True:
            now = datetime.now(timezone.utc)
            minutes = (time.time() - start) / 60
            factor = args.drop if (args.incident_after is not None and minutes >= args.incident_after) else 1.0
            v = noisy(now, factor)
            put([(now, v)])
            state = cw.describe_alarms(AlarmNames=[ALARM]).get("MetricAlarms", [])
            state = state[0]["StateValue"] if state else "no alarm"
            tag = "  <- incident" if factor < 1 else ""
            print(f"{now.astimezone(LOCAL_TZ):%a %H:%M}  {v:7.0f} rpm   alarm: {state}{tag}")
            time.sleep(PERIOD)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_cleanup(_):
    cw.delete_alarms(AlarmNames=[ALARM])
    cw.delete_anomaly_detector(SingleMetricAnomalyDetector=detector_spec())
    print("Alarm and detector deleted. The metric's data expires on its own;")
    print("custom metrics stop costing once no new data is sent.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ["backfill", "detector", "status", "alarm", "cleanup"]:
        sub.add_parser(name)
    lv = sub.add_parser("live")
    lv.add_argument("--incident-after", type=float, default=None,
                    help="minutes of normal traffic before the drop starts")
    lv.add_argument("--drop", type=float, default=0.45,
                    help="traffic multiplier during the incident, e.g. 0.15 = an 85%% drop")
    args = p.parse_args()
    {"backfill": cmd_backfill, "detector": cmd_detector, "status": cmd_status,
     "alarm": cmd_alarm, "live": cmd_live, "cleanup": cmd_cleanup}[args.cmd](args)
