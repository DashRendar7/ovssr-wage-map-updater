# OVSSR Davis-Bacon wage map: daily SAM.gov check

This project keeps the Laborer rates on the map at https://huggingface.co/spaces/DanielK1320/OVSSRDBRates current.

Every morning GitHub runs update.py:

1. It asks SAM.gov for the current modification of every Davis-Bacon general wage determination the map uses, and confirms each document it downloads is that exact modification.
2. It re-reads every rate on the map, with two separately written readers (readers.py). A rate changes on the map only when both readers agree.
   If SAM.gov's listing looks incomplete (a state shows far fewer determinations than the day before), the run stops without changing anything.
3. When they don't agree, or a county is no longer listed, the county keeps its last confirmed rate and is marked for a hand check.
4. Counties that carried a hand-written note are never changed automatically. They are marked instead.
5. The results go into data/rows.json (the rates) and data/status.json (the log), which the map reads.

The starting point is the rate set verified by hand on Nov. 16, 2026.

- **Run it now:** Actions tab, "Check SAM.gov for wage changes", Run workflow.
- **Self-test:** tick the self-test box when you run it. Every determination is re-read, and the run reports whether both readers reproduce the verified rates. A self-test changes no rates.
- **If a run fails**, GitHub emails the owner. The map keeps showing the last confirmed rates.

These are federal minimums for federally funded or assisted work, not union pay. Check the determination itself before relying on a rate in a bid, a complaint or a publication.
