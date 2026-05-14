"""
WKO5 Reverse CLI — Milestone 1

Usage:
    python cli.py import <file.fit>           parse and store workout
    python cli.py list [--last N]             list recent workouts
    python cli.py show <date|id>              show workout details
    python cli.py mmp <date|id>              show full MMP curve
    python cli.py athlete                     show/set athlete profile
    python cli.py athlete set-ftp <watts>     set FTP
    python cli.py validate <file.fit>         compare MMP against WKO5 (dev)
"""

import json
import sys
import os

# Ensure src/ is on path when running directly
sys.path.insert(0, os.path.dirname(__file__))


def cmd_import(args):
    if not args:
        print("Usage: wko import <file.fit> [--force]")
        sys.exit(1)

    path = args[0]
    force = "--force" in args

    from importer import import_fit
    result = import_fit(path, force=force)

    status = result["status"]

    if status == "skipped":
        print(f"ℹ  Already imported: {result['workout_id']}")
        return

    print(f"✓  Imported: {result['workout_id']}")
    m = result.get("metrics", {})

    if m.get("avg_power_w"):
        duration_s = m.get("duration_s", 0)
        mins, secs = divmod(int(duration_s), 60)
        hrs, mins = divmod(mins, 60)
        dur_str = f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}"
        print(f"   Duration:   {dur_str}")
        print(f"   Avg Power:  {m['avg_power_w']} W")
        print(f"   NP:         {m['normalized_power_w']} W")
        if m.get("tss") is not None:
            print(f"   TSS:        {m['tss']}")
        if m.get("avg_hr_bpm"):
            print(f"   Avg HR:     {m['avg_hr_bpm']} bpm")

    if result.get("mmp_5min"):
        print(f"   MMP 5min:   {result['mmp_5min']} W  |  "
              f"20min: {result.get('mmp_20min', '—')} W  |  "
              f"60min: {result.get('mmp_60min', '—')} W")

    for w in result.get("warnings", []):
        print(f"   ⚠  {w}")


def cmd_list(args):
    last = 10
    for i, a in enumerate(args):
        if a == "--last" and i + 1 < len(args):
            last = int(args[i + 1])

    from storage import load_index
    workouts = load_index()
    if not workouts:
        print("No workouts imported yet.")
        return

    recent = workouts[-last:][::-1]
    print(f"{'Date':<12} {'Sport':<10} {'Duration':<10} {'NP':>6} {'TSS':>7}")
    print("-" * 50)
    for w in recent:
        dur = w.get("duration_s") or 0
        mins, secs = divmod(int(dur), 60)
        hrs, mins = divmod(mins, 60)
        dur_str = f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}"
        np_val = f"{w['normalized_power_w']:.0f}W" if w.get("normalized_power_w") else "—"
        tss_val = f"{w['tss']:.1f}" if w.get("tss") is not None else "—"
        print(f"{w.get('date','?'):<12} {w.get('sport','?'):<10} {dur_str:<10} {np_val:>6} {tss_val:>7}")


def cmd_show(args):
    if not args:
        print("Usage: wko show <date|id>")
        sys.exit(1)

    query = args[0]
    from storage import load_index, load_workout

    workouts = load_index()
    match = next(
        (w for w in workouts if w.get("date") == query or w.get("id", "").startswith(query)),
        None
    )
    if not match:
        print(f"No workout found for: {query}")
        sys.exit(1)

    detail = load_workout(match["id"])
    if not detail:
        print("Workout file missing.")
        sys.exit(1)

    print(json.dumps(detail, indent=2))


def cmd_mmp(args):
    if not args:
        print("Usage: wko mmp <date|id>")
        sys.exit(1)

    query = args[0]
    from storage import load_index, load_workout

    workouts = load_index()
    match = next(
        (w for w in workouts if w.get("date") == query or w.get("id", "").startswith(query)),
        None
    )
    if not match:
        print(f"No workout found for: {query}")
        sys.exit(1)

    detail = load_workout(match["id"])
    mmp = detail.get("mmp_curve", {})
    if not mmp:
        print("No MMP data.")
        return

    _LABELS = {
        "1": "1s", "5": "5s", "10": "10s", "30": "30s", "60": "1min",
        "120": "2min", "300": "5min", "600": "10min",
        "1200": "20min", "1800": "30min", "3600": "60min",
    }
    print(f"\nMMP Curve — {match['date']}")
    print(f"{'Duration':<12} {'Power (W)':>10}")
    print("-" * 25)
    for d, v in sorted(mmp.items(), key=lambda x: int(x[0])):
        label = _LABELS.get(d, f"{d}s")
        print(f"{label:<12} {v:>10.1f}")


def cmd_athlete(args):
    from storage import load_athlete, save_athlete

    if not args:
        profile = load_athlete()
        print(json.dumps(profile, indent=2))
        return

    if args[0] == "set-ftp" and len(args) >= 2:
        ftp = float(args[1])
        profile = load_athlete()
        profile["ftp_w"] = ftp
        save_athlete(profile)
        print(f"✓ FTP set to {ftp} W")
    elif args[0] == "set-weight" and len(args) >= 2:
        weight = float(args[1])
        profile = load_athlete()
        profile["weight_kg"] = weight
        save_athlete(profile)
        print(f"✓ Weight set to {weight} kg")
    else:
        print("Unknown athlete command. Try: athlete set-ftp <watts>")


def cmd_validate(args):
    """
    Development command: parse a .fit and show MMP values for manual comparison
    against WKO5.
    """
    if not args:
        print("Usage: wko validate <file.fit>")
        sys.exit(1)

    from fit_parser import parse_fit
    from mmp import compute_mmp
    from metrics import normalized_power

    raw = parse_fit(args[0])
    if not raw.has_power:
        print("No power data in file.")
        return

    mmp = compute_mmp(raw.power_w, raw.time_s)
    np_val = normalized_power(raw.power_w)

    print(f"\nValidation output for: {args[0]}")
    print(f"Duration: {raw.duration_s:.0f}s | NP: {np_val} W")
    print(f"\n{'Duration':<12} {'Our MMP (W)':>12}  {'WKO5 (enter)':>14}  {'Error':>8}")
    print("-" * 55)

    key_durations = [1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600]
    for d in key_durations:
        if d > raw.duration_s:
            break
        our_val = mmp.get(d, 0.0)
        label = _fmt_dur(d)
        wko5_input = input(f"  {label:<10} {our_val:>12.1f}  Enter WKO5 value (or skip): ").strip()
        if wko5_input:
            try:
                wko5_val = float(wko5_input)
                err = abs(our_val - wko5_val) / wko5_val * 100
                ok = "✓" if err <= 1.0 else "✗"
                print(f"    {ok} Error: {err:.2f}%")
            except ValueError:
                pass


def _fmt_dur(s: int) -> str:
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}min"
    return f"{m // 60}h{m % 60:02d}min"


COMMANDS = {
    "import": cmd_import,
    "list": cmd_list,
    "show": cmd_show,
    "mmp": cmd_mmp,
    "athlete": cmd_athlete,
    "validate": cmd_validate,
}


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = args[0]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(1)

    COMMANDS[cmd](args[1:])


if __name__ == "__main__":
    main()
