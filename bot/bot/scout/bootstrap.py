"""One-off import of tape already on disk, so the scout does not start blind.

The arcus-mm recorder (retired) captured BBO and trades for 20 Arcus perps from 2026-09-19; its REST pulls hold ~30 days
of trades for every perp. Both are read-only sources here: files are parsed into the scout's own TapeStore and never
modified. Part names are stable, so re-running the import overwrites instead of duplicating.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from bot.scout.tape import TapeStore, import_recorded_bbo, import_trade_lines


def _jobs(src: Path) -> list[tuple[str, str, list[Path], list[Path]]]:
    """(market, source day, bbo files, trade files) for every recorded market-day."""
    jobs = []
    for base in ("raw", "compressed"):
        root = src / "data" / base
        if not root.exists():
            continue
        for day_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for mdir in sorted(p for p in day_dir.iterdir() if p.is_dir() and p.name.endswith("-USD")):
                bbo = [p for p in (mdir / "bbo.jsonl", mdir / "bbo.jsonl.gz") if p.exists()]
                tr = [p for p in (mdir / "trades.jsonl", mdir / "trades.jsonl.gz") if p.exists()]
                if bbo or tr:
                    jobs.append((mdir.name, f"{base}-{day_dir.name}", bbo, tr))
    return jobs


def _run(args: tuple[str, str, str, list[Path], list[Path]]) -> tuple[str, str, int, int]:
    root, market, part, bbo_files, trade_files = args
    store = TapeStore(root)
    nb = nt = 0
    if bbo_files:
        b = import_recorded_bbo(bbo_files)
        store.write_part(market, "bbo", f"arcusmm-{part}", b)
        nb = len(b["ts"])
    if trade_files:
        t = import_trade_lines(trade_files)
        store.write_part(market, "trades", f"arcusmm-{part}", t)
        nt = len(t["ts"])
    return market, part, nb, nt


def import_arcusmm(src: Path, store: TapeStore, *, workers: int = 6, markets: set[str] | None = None,
                   history: bool = True) -> list[tuple[str, str, int, int]]:
    jobs = [(str(store.root), m, part, b, t) for m, part, b, t in _jobs(src) if not markets or m in markets]
    hist = src / "data" / "history" / "trades"
    if history and hist.exists():
        for f in sorted(hist.glob("*-USD.jsonl.gz")):
            m = f.name.removesuffix(".jsonl.gz")
            if not markets or m in markets:
                jobs.append((str(store.root), m, "rest-history", [], [f]))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_run, jobs))
