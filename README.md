# OVSSR Davis-Bacon wage map: daily SAM.gov check

This project keeps the Laborer rates on the map at https://huggingface.co/spaces/DanielK1320/OVSSRDBRates current.

Every morning GitHub runs update.py:

1. It asks SAM.gov which Davis-Bacon general wage determinations have a new modification.
2. It re-reads only those, with two separately written readers (readers.py). A rate changes on the map only when both readers agree.
3. When they don't agree, or a county is no longer listed, the county keeps its last confirmed rate and is marked for a hand check.
4. Counties that carried a hand-written note are never changed automatically. They are marked instead.
5. The results go into data/rows.json (the rates) and data/status.json (the log), which the map reads.

The starting point is the rate set verified by hand on Nov. 16, 2026.

- **Run it now:** Actions tab, "Check SAM.gov for wage changes", Run workflow.
- **Self-test:** tick the self-test box when you run it. Every determination is re-read, and the run reports whether both readers reproduce the verified rates. A self-test changes no rates.
- **If a run fails**, GitHub emails the owner. The map keeps showing the last confirmed rates.

These are federal minimums for federally funded or assisted work, not union pay. Check the determination itself before relying on a rate in a bid, a complaint or a publication.
