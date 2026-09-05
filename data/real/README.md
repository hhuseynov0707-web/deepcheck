# Recorded real sessions

Ground truth for `backend/evaluate.py`. **The directory a file sits in is its
label** — `human/` or `bot/` — so a misfiled session silently corrupts every
number the evaluation reports.

Collect them like this:

1. Run the stack (`docker-compose up`) and open http://localhost:3000/demo.
2. **Humans:** have real people complete the checkout form. Aim for 30+
   sessions across different people, mice, trackpads, laptops and phones —
   a set collected from one person on one machine measures that machine.
3. **Bots:** `python tools/bot_session.py --variant naive` and
   `--variant jitter`, 15+ runs each.
4. Freeze what landed in Postgres:

   ```bash
   cd backend
   python record_session.py --list
   python record_session.py --label human <session-id> [<session-id> ...]
   python record_session.py --label bot   --since 2026-09-05T14:00:00
   ```

5. Measure and publish:

   ```bash
   cd backend && python evaluate.py --markdown ../docs/evaluation.md
   ```

The JSON files themselves are gitignored: they are behavioral recordings of
real people, and they do not belong in a public repository. Keep them
locally, publish only the aggregate report.
