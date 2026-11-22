# Dynamic alert thresholds

Fixed alert thresholds page you for normal behavior and stay silent during real outages.
This folder reproduces the experiment from the video and article:

| Script | What it does | Needs AWS? |
| --- | --- | --- |
| `simulate.py` | 3 weeks of simulated traffic; compares a static rule with a dynamic band | No |
| `k_sweep.py` | Replays the same data at K = 1 to 8; false alarms vs. incidents caught | No |
| `cloudwatch_lab.py` | Sends the same traffic to AWS CloudWatch and uses its anomaly detection | Yes |

Results on the simulated data (last 16 days):

| Rule | False alarms | Incidents caught |
| --- | --- | --- |
| Static (> 950 or < 100 req/min) | 67 | 0 of 2 |
| Dynamic, K = 4 | 3 | 2 of 2 |

- Video: [Watch on YouTube](https://www.youtube.com/watch?v=kbJgkAzOjqg)
- Article: *link coming*

## Run locally (no AWS)

Python 3.10 or newer.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python simulate.py     # prints the scorecard, writes threshold_comparison.png
python k_sweep.py      # prints the K table, writes k_sweep.png
```

Change `K`, `SEED` or the incidents at the top of `simulate.py` and run it again.

## Run it in AWS CloudWatch

Costs well under $1 for a few days (one custom metric, one alarm). Use a personal
AWS account, not a work one.

1. **Secure the account.** Turn on MFA for the root user and set a $5 budget alert
   in Billing → Budgets. On newer project-based AWS accounts, note your project's
   home Region: calls to other Regions fail with `AccessDenied`.
2. **Create a lab user.** IAM → Users → `lab-user`, no console access, attach only
   `CloudWatchFullAccess`, then create an access key for the CLI.
3. **Connect the CLI.**
   ```bash
   aws configure              # lab-user's key, your Region, output json
   aws sts get-caller-identity
   ```
4. **Back-fill once.** Sends two weeks of history ending now. Run it one time only.
   ```bash
   python cloudwatch_lab.py backfill
   ```
5. **Turn on anomaly detection and start live data right away.**
   ```bash
   python cloudwatch_lab.py detector
   caffeinate -i python cloudwatch_lab.py live    # macOS; on Linux/Windows drop caffeinate
   ```
6. **Wait for training.** Run `python cloudwatch_lab.py status` until it says `TRAINED`,
   then check the band in the console: CloudWatch → Metrics → Classic metrics →
   Custom namespaces → `TheLeanSignal/Lab`, period 5 minutes, and add the expression
   `ANOMALY_DETECTION_BAND(m1, 2)`.
7. **Create the alarm and run an incident.** Wait until the alarm shows OK for about
   an hour, then:
   ```bash
   python cloudwatch_lab.py alarm
   caffeinate -i python cloudwatch_lab.py live --incident-after 30 --drop 0.15
   ```
   Traffic stays normal for 30 minutes, then falls to 15%. The alarm should go
   to In alarm 10 to 15 minutes later.
8. **Clean up.**
   ```bash
   python cloudwatch_lab.py cleanup
   ```
   Then delete `lab-user`'s access key in IAM.

### Settings

At the top of `cloudwatch_lab.py`:

- `LOCAL_TZ`: the time zone your daily traffic pattern follows (default `America/Chicago`).
- `METRIC`: rename it (for example, add `V3`) to start over with a clean metric.
- `BAND_WIDTH`: CloudWatch's version of K. Default 2.

### Lessons learned

- Back-filled data older than a day can take up to 48 hours to become readable.
- Never back-fill into a gap. It broke the model; a fresh metric fixed it.
- A newly trained model is noisy for the first hours. Don't page on it on day one.
- At bandwidth 2, a 55% traffic drop stayed inside the band. Check what your band
  would actually catch before you trust it.

## Safety

- Keys live in `~/.aws/credentials`, outside this repo. Never commit them.
- Use the CloudWatch-only `lab-user`, never root keys.
- Delete the access key when you're done.

## License

MIT. See [LICENSE](LICENSE). All data is simulated.
