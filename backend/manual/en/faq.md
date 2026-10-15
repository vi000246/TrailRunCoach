---
id: faq
order: 4
title: FAQ
summary: Sync failing, a missing activity, why the schedule looks like this, numbers that differ from the watch, too many tips, heat acclimation, importing and exporting files, background sync… each with a link to the full explanation.
icon: faq
---
Where people most often get stuck. Each question starts with the answer; its last line links to the part of the manual that explains it in full. Open “In depth” for the details.

| Group | Questions |
|---|---|
| Sync | [Sync fails](#sync-failed), [An activity is missing, twice, or the wrong type](#missing-activity), [How soon new activities arrive](#background-sync), [A number says “Calculating”](#calculating) |
| Schedule | [Why the schedule looks like this](#why-this-plan), [Why a recovery week](#recovery-week), [No intervals in Base](#no-intervals), [What “Plan to confirm” is](#plan-confirm), [The sessions are not on the watch](#not-on-watch) |
| Numbers | [Numbers differ from the watch](#watch-numbers), [Changed a threshold, old numbers stayed](#threshold-change), [Without a Stryd](#no-power), [What “estimate” means](#estimate) |
| Other | [Too many tips](#too-many-hints), [Heat acclimation](#heat), [Importing or exporting files](#import-export), [Injured or ill](#injury) |

## Sync fails — what now? {#sync-failed}

Most often the **login expired**: when the bar at the top says “COROS login expired: new activities can't sync”, log in again in Settings → Data & sync and tick “Remember password”, so later expiries log in again by themselves.

1. Settings → Data & sync: look at the status next to your data source.
2. “Login expired”: enter your account and password again and press “Log in” (tick “Remember password”).
3. Once logged in, press “Sync now”.

More: [Connecting a data source](start.md#connect), [How long the first sync takes](start.md#first-sync).

:::deep
### Other messages
- **COROS blocked the account for a while** (too many requests, or the login was refused): to protect the account, the next daily scheduled sync tries again; until then, don't press sync over and over. When COROS refuses often, the whole site pauses for about 30 minutes; the activities already imported stay.
- **Garmin (trial)**: 1 automatic and 3 manual “Sync now” a day; a long history comes over several days (“today's download limit is reached, the rest continues tomorrow”). When Garmin locks the login it usually comes back in 2–3 days, and sync pauses meanwhile. The code goes to your email or phone and is valid for 10 minutes.
- **Password changed**: “logging in again with the remembered password failed too” = the password probably changed; log in again.

### Remember password
Ticked, the password is stored encrypted on the server; unticking or logging out deletes it at once. A COROS login expires after about 24 hours; ticked, it logs in again once by itself.
:::

## An activity is missing, shows twice, or has the wrong type? {#missing-activity}

**Missing**: Settings → Data & sync → “Full check (find missed activities)” compares every activity with the watch platform; press “Download missing” for the ones not here. **Wrong type, tags**: Activities → “Edit” on that activity.

1. Missing: press “Full check”, wait 40–60 seconds, press “Download missing”.
2. Activities that failed are listed under “Activities that failed to download”; press “Retry”.
3. Type, tags, pack, terrain: Activities → “Edit”; changes save at once.

More: [How long the first sync takes](start.md#first-sync).

:::deep
### When a download fails
Later syncs retry only the failed ones, each at most 5 times or for 7 days, then they stop; press “Retry” to try again. With scheduled sync on (when the site opens or daily), the last 60 days are also checked every week and missing ones downloaded. The full check only lists activities that are here but not on the platform; it never deletes them.

### The same activity twice
Importing the same file again never adds it twice, and a run that came both by sync and by import counts once. When the app did not notice, open the activity's editor and under “The same activity from other sources” choose “Same activity (merge)”; if it guessed wrong, “Not the same (split)”.

### Automatic and manual
Fields the app set are marked “Auto” (hover for the reason), the ones you changed “Manual”; choosing “Auto” again goes back. The original FIT file is never changed. “Exclude” is only a mark: the activity leaves load and charts but stays in the list.
:::

## How soon do new activities arrive? (Background sync) {#background-sync}

With “Sync new activities in the background” on, the server looks for new activities regularly — **about every 30 minutes for accounts used lately** — syncs them and gets the overview and the schedule matches ready. In a hurry, press “Sync now”.

1. Settings → Data & sync: “Sync new activities in the background”, “Sync when you open the site” and the daily sync at a set time.
2. Opening the site syncs when the last sync is older than the hours you set (6 by default).

More: [Connecting a data source](start.md#connect).

:::deep
### How often
Accounts used in the last 3 days about every 30 minutes; less-used ones every 2–6 hours; after a month without use only the daily one at a set time. Nothing between 1 and 5 a.m. When COROS blocks for a while, the next look is postponed. Accounts are spread out and synced one at a time.

### Exceptions
- Garmin allows one scheduled sync a day, so it is not checked in the background.
- Free plan: one sync a night (between 1 and 5 a.m.), no background checks, and opening the site does not sync.
:::

## The Chronic load says “Calculating”? {#calculating}

On the first sync the app computes the **last 90 days** first so you can start right away, and adds the older activities in the background. The Chronic load builds up over your whole history, so until they are all in it says “Calculating” — nothing is broken.

When the bar says the older activities are complete, reload the page.

More: [How long the first sync takes](start.md#first-sync), and [Chronic load](glossary.md#chronic-load) in the glossary.

:::deep
### Why it waits for the whole history
The Chronic load is an exponentially weighted average with a 42-day time constant; its starting point affects months of numbers, and starting from only 90 days would come out low. If you have only a few weeks of data but know the value from another platform, enter a “Starting chronic load” in Settings › Physiology.
:::

## Why does the schedule look like this? {#why-this-plan}

The schedule follows your **season plan**, your **Chronic load**, your **plan preferences** and **what you actually did**. To see why a session changed, hover the card on a computer and read its “Changes” part; every automatic adjustment is in the “Automatic adjustment log”, where it can be undone.

1. Phases (Base, Build, Taper) are counted back from the A race on the Season plan page.
2. Each week adds volume within the Chronic load ramp cap; a low Load balance turns the week into a recovery week.
3. Training days, weekly hours, long-run day: “⚙ Plan preferences”.
4. Missed or extra sessions: press “Replan from progress”, preview, then apply.

More: [Where each week's volume comes from](schedule.md#weekly-load), [Replanning and automatic adjustment](schedule.md#replan), [Plan preferences](schedule.md#prefs), and [Phases](glossary.md#phases) in the glossary.

:::deep
### What the card shows
Hovering a card shows: how it went, target, how to run it, changes, watch, based on. “Changes” says why the session was adjusted (for example fatigue protection, or a hard session moved after a run you rated very hard).

### Sessions you changed
Automatic adjustment and replanning never change the content of sessions you edited or added; to turn them back to automatic too, tick the reset option when replanning, or right-click → “Revert to automatic”.
:::

## Why is this a recovery week? {#recovery-week}

Two things plan a recovery week: **the rhythm** (in Base, 1 week after every 3), or **Load balance below −30** (you trained a lot more than usual lately and fatigue has built up). A recovery week is about 60–70 % of the usual volume.

More: [Where each week's volume comes from](schedule.md#weekly-load) (with a chart you can drag), and [Load balance](glossary.md#load-balance) in the glossary.

:::deep
### Two kinds
- Rhythm: in Base 1 week in 4 (70 % of the 3 weeks' average); in Build, counted back from the race, at most 6 weeks without one.
- Fatigue: Load balance < −30 turns the next week into a recovery week (60 % of the last 4 weeks' average; weeks with a back-to-back excepted); between −30 and −20 the volume is held.

If you don't feel tired, you can change the recovery week's sessions; the app remembers they are yours.
:::

## Why are there no intervals in Base? {#no-intervals}

Base builds the aerobic foundation first, behind an **interval gate**: by default “Auto”, which waits until your aerobic base is there, and then at most 1 interval session a week. Next to the interval gate in “⚙ Plan preferences” it says whether your data passes now, and why.

More: [Plan preferences](schedule.md#prefs), and [Aerobic threshold (AeT)](glossary.md#aet) in the glossary.

:::deep
### How “Auto” decides
With a measured AeT (within 16 weeks) and an LTHR that is not the default: Uphill Athlete's gap method (LTHR ÷ AeT − 1 ≤ 10 %) or Friel's drift (< 5 %). Without a measured AeT there is no gate; guardrails decide instead: ≥ 75 % low intensity, Chronic load up less than 5 a week, a weekly ramp ≤ 10 %, the Load balance. The plan preferences offer other methods too (UA gap, Friel drift, 90′ long run, aerobic plateau, weeks, no gate).

### Getting there sooner
Do an AeT test (40 minutes at a steady power, watching the HR drift); with a measured value the gate is far more accurate.
:::

## What is “Plan to confirm”? {#plan-confirm}

For **big changes** the automatic adjustment asks first instead of changing the schedule: “Plan to confirm” appears on the schedule, and you approve (apply and push) or reject (keep the old sessions).

More: [Replanning and automatic adjustment](schedule.md#replan).

:::deep
### What counts as big
- A week's planned load 20 % or more above the last pushed version;
- within 14 days of an A race, removing a long run, a hard session or a test, or moving a hard session;
- a phase change;
- more than 3 changes in the push range that are not reductions.

### Where you are told
The plan preferences' “Approval notice”: “Watch” pushes a 1-minute “Plan to confirm” session to the watch to remind you; “Overview page only” shows it on the Overview only. Not wanting to be asked: turn off “Ask me before big changes”.
:::

## The sessions are not on the watch? {#not-on-watch}

The schedule reaches the watch only when **pushed**: log in to COROS in Settings → Data & sync, then press “Push to watch” on the schedule; the sessions show in the watch's calendar after the watch app syncs. A red watch icon on a card = the push failed.

More: [Sending the schedule to the watch](schedule.md#push).

:::deep
### Common causes
- The COROS login expired: see [Sync fails](#sync-failed).
- Pushing works with COROS only for now; Garmin and intervals.icu are not open yet.
- Strength sessions, rest days and races without steps are not pushed.
- To push every time: turn on “push to the watch automatically” in the plan preferences; after an automatic adjustment the next N days (7 by default) are pushed.
:::

## Numbers differ from the watch? {#watch-numbers}

That is expected: **the two sides compute differently and use different thresholds**. The usual four: Training load, heart-rate zones, max HR, training effect. Settings → Physiology → Heart rate has “App vs. watch values”, which warns when the two differ by more than 5 bpm.

- **Training load**: here “an hour at threshold = 100”, moving time only, climbing adds to it; it becomes COROS's TL only when pushed (an estimate, about ±20 %).
- **Heart-rate zones**: the schedule sends bpm numbers to the watch; the zones the watch and the COROS app show use the watch's own settings.
- **Training effect**: 1–10 here, 0–5 on the watch.

More in the glossary: [Training load](glossary.md#training-load), [Zones](glossary.md#zones), [Aerobic / Anaerobic training effect](glossary.md#training-effect); and [Max and resting heart rate](start.md#heart-rate) in Getting started.

:::deep
### Making the two agree
Set the watch's heart-rate zones like the ones here: in the COROS app, profile → settings → heart rate zones. Max HR here comes from, in order: yours → the watch → the highest HR held 5 seconds or more in the last 365 days of running → the age formula (an estimate).

### COROS's Training Load (TL)
The default conversion comes from one runner's activities (an estimate); after each sync it is refitted on your own COROS activities that have TL, and a new fit replaces the old one only if it is no worse on the last 30 days.
:::

## I changed a threshold — why didn't the old numbers change? {#threshold-change}

Thresholds (LTHR, AeT, CP) **take effect from the test date and are not applied backwards**: earlier activities keep the values of their time, so old Training load does not move because you changed a threshold today. That is on purpose, so history stays put.

More: [Thresholds](start.md#thresholds).

:::deep
### Seeing when it changed
Settings › Physiology has “Threshold changes”, listing which threshold changed on which day. On the day an estimated LTHR was replaced by a measured one, the Training load chart draws a dashed line, because the HR load's scale changed that day. Max and resting HR work the same: from today on, earlier charts keep the old values.
:::

## Can I use it without a Stryd power meter? {#no-power}

Yes. **Without a Stryd it uses heart rate**: Training load becomes HR load (or pace load), and the schedule's targets use heart-rate zones. What only power gives (the CP model, power curves, Anaerobic reserve, training effect) is hidden.

More: [Training load](glossary.md#training-load) and [Modelled threshold power](glossary.md#mtp) in the glossary; [Weight and basic info](start.md#weight) (power source) in Getting started.

:::deep
### The watch's power
Watch-estimated power stays out of the power model by default; to use it, turn on “use watch-estimated power in the power model” under Settings › Physiology › Power › Advanced, and the numbers are treated as estimates. Turning off “Use power” hides the power-only charts.

### Activities with neither
An activity with neither power nor heart rate can get a Training load from perceived exertion: see [RPE](glossary.md#rpe) in the glossary.
:::

## What does “estimate” mean? {#estimate}

A number or rule marked “estimate” has **no direct published source**: it comes from your data or common practice, and is calibrated on your own data later. It is not wrong — read it as a direction.

More: the [Glossary](glossary.md) (every term's “In depth” gives its sources).

:::deep
### The small tags
Auto-estimated numbers may carry: “Personal n=…” (from that many of your own data points), “Manual” (yours), “Default (literature)” (a sourced default), “Default (estimate)” (a default without a source). All the methods' sources are on the About page under the methods and sources.
:::

## Too many tips? {#too-many-hints}

Settings → Personal → “How many tips” → **Lean**. Safety tips (a red pain light, Plan to confirm, cut-off times) show at every level; the rest go under “Details”.

1. Settings is under “More” in the top menu (on a phone, in the bottom row).
2. Personal → “How many tips”: Full / Standard / Lean, or “Back to automatic”.
3. A single tip: “Got it” hides it (it comes back only when its condition changes).

More: [First use](start.md#first-run) (where Settings is), [Replanning and automatic adjustment](schedule.md#replan) (Plan to confirm).

:::deep
### What “automatic” means
New runners (no 4 weeks of running on record yet) get “Full”, everyone else “Standard”; “Lean” is never picked automatically. A page shows at most 3 reminders, the rest folded into “More reminders”; the “Too many tips?” link next to a tip takes you to this setting.

### Suggestions
The Overview's suggestions have “Remind me later” (7 days), “No thanks”, or ✕. Tests you no longer want suggested are listed in Settings under “Tests no longer suggested”; “Restore” brings one back.
:::

## What do I do for heat acclimation? {#heat}

**Nothing**: heat acclimation is fully automatic. The schedule plans no heat sessions and there is nothing to tick; the app works it out from the time you run in the heat, and shows it on the Overview's heat acclimation card.

More: [Plan preferences](schedule.md#prefs) (there is no heat option there).

:::deep
### How it is computed
Every minute moving in the heat (air temperature plus dew point high enough) counts as heat exposure; 60 minutes = a full day's dose, and without exposure it fades about 2.5 % a day. The Overview card shows the days of heat exposure in the last 14 days and the level (acclimated ≥ 75 %, partly ≥ 35 %, not acclimated).

### A hot race
When an A or B race within 30 days is forecast hot and your projected acclimation is under 75 %, the Overview reminds you to slow down and drink more on the day; the race calculator already counts the heat. This is an estimate.
:::

## How do I import or export files? {#import-export}

**Import**: Settings → Data & sync → “Import FIT files”, for files or a whole folder (TCX and GPX too); a whole account's export ZIP (Garmin, COROS…) goes through “Import an account export”. **Export**: Settings → Personal → Account → “Download my data”.

More: [Connecting a data source](start.md#connect) (without a COROS or Garmin account).

:::deep
### Import
- At most 20 MB a file and 3000 files at a time; the same file imported again never makes two.
- Desktop Chrome and Edge can set an auto-import folder: new files are imported each time you open the site (same computer, same browser).
- “Import an account export” takes a ZIP of up to 4 GB and resumes after an interruption.

### Export
“Download my data” is one zip: settings, season plan, schedule, notes, injury log, uploaded GPX, activities and FIT originals, account details and consent history (no login credentials or remembered passwords). “Download all my FIT files” packs only the FIT files.

### Archiving
Activities older than “Activity retention” (3 years by default) lose their FIT original on the 1st of each month; summaries and long-term charts stay. Download the originals first if you want them.
:::

## Injured or ill — what now? {#injury}

Record it in the Injury log: the severity decides how the schedule changes (mild = train as usual, moderate = less or different training, severe = no running). For travel or days you just can't train, use “⋯ → Set no-training days”. Coming back after a break, the schedule adds the volume back in stages.

More: [Replanning and automatic adjustment](schedule.md#replan) (no-training days), [Changing a session](schedule.md#edit).

:::deep
### Pain lights
- Green: planned as usual.
- Yellow: the rest of the week no longer than last week, no intervals, long run × 0.75.
- Red: no running, no-training days offered; later, “ready to walk-run” starts from walk-runs.

### Illness
A mild cold: only Z1 runs of 45 minutes or less. Fever or whole-body symptoms: no running until at least a day after they are gone, then a first run of 30 minutes or less at recovery pace.

### After a break
1–5 days off: straight back to the usual volume; 6–13 days: 50 %, then 75 %; longer breaks in more stages (after Jack Daniels' return-to-training table). None of this is a medical diagnosis — see a doctor when it really hurts.
:::
