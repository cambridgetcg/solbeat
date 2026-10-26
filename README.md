# SolBeat 🌞

SolBeat is a quiet, durable heartbeat for a local Codex agent. A cheap Hermes scheduler tick asks SolBeat whether anything meaningful changed. No signal means no model run and no message; a real signal becomes one bounded, leased Codex turn that can later be delivered through WhatsApp.

```text
local event producer
        │
        ▼
owner-only SQLite ledger ── atomic dedupe / lease / retry / dead-letter
        │
        ▼
Hermes pre-run gate ── wakeAgent:false ──► no model, no message
        │
        └────────────── wakeAgent:true ───► Codex turn ───► WhatsApp
```

## What it does

- Gives scripts, agents, and local services one tiny interface for emitting useful events.
- Deduplicates active signals and leases a batch atomically, including across concurrent scheduler ticks.
- Preserves failed work with expiry, exponential backoff, bounded attempts, and a retained dead-letter state.
- Respects quiet hours and minimum wake spacing; urgent events can bypass both.
- Exposes only bounded, redacted summaries to the model. Raw redacted payloads remain in owner-only local state.
- Supports an optional gentle periodic check-in without turning every scheduler tick into a model call.

## What it does not do

- It does not make a Codex process continuously conscious. Hermes v0.17 starts a fresh Codex thread for each cron run; continuity comes from the durable ledger and local project state.
- It does not grant an unattended agent new authority. Event summaries are data, not instructions or permission.
- It does not guarantee that every secret shape is detected. Keep credentials out of events; redaction is a second line of defence.
- It does not guarantee exactly-once WhatsApp delivery. `ack` proves local handling, while the downstream chat delivery can still fail or repeat.
- It does not make Baileys an official WhatsApp integration. A linked-device bot can be restricted by WhatsApp; use a dedicated number and a strict allowlist.
- Lease resolution is batch-level in v0.1: one `ack` or `release` applies to every event in that leased batch.

## Local use

The repo wrapper needs Python 3.11 or newer and has no runtime dependencies.

```bash
bin/solbeat init
bin/solbeat emit \
  --source local \
  --kind useful-change \
  --summary "A bounded thing worth looking at" \
  --dedupe-key example-1
bin/solbeat gate
bin/solbeat status
bin/solbeat doctor
```

`gate` always emits exactly one JSON line. Hermes treats a final `{"wakeAgent":false}` as a silent tick. A true gate includes a lease ID and summary-only events. After verified work:

```bash
bin/solbeat ack LEASE_ID --receipt "short outcome"
```

If work is blocked, release it instead of losing it:

```bash
bin/solbeat release LEASE_ID --reason "short reason"
```

Pause and resume are non-destructive. A timed pause makes rest self-releasing:

```bash
bin/solbeat pause --reason "resting"
bin/solbeat pause --for-minutes 30 --reason "recharging"
bin/solbeat resume
```

Pause keeps accepting and preserving events but creates no new leases,
including for urgent events. A timed pause becomes eligible to resume at its
deadline; the next `gate` clears it and applies the normal quiet-hour and
wake-spacing rules. It does not stop the scheduler, cancel an existing lease,
delete queued work, or promise a background action at the exact deadline.
Timed pauses are bounded at 365 days. `status` reports the effective pause
state and its deadline without exposing the private reason.

The source-tree wrapper explicitly uses
[`config/solbeat.toml`](config/solbeat.toml), preserving this device's policy
and live state under `~/.hermes/profiles/sol/solbeat`. An installed `solbeat`
command instead uses a neutral package-local default: UTC policy, proactive
check-ins disabled, and separate state under `~/.local/state/solbeat`. The
installed command accepts `--config PATH`, `--home PATH`, or their environment
variables. The specialized source wrapper clears ambient `SOLBEAT_CONFIG` and
`SOLBEAT_HOME` so shell state cannot silently redirect the live ledger; an
explicit CLI flag remains a deliberate override. State directories use mode
`0700` and database files use mode `0600`. Unknown or misplaced TOML keys fail
fast so a typo cannot silently change the wake policy.

This device enables one gentle daily pulse, first due about 18 hours after the scheduler begins using the policy. It remains inside quiet-hour and wake-spacing rules, and its prompt explicitly permits `[SILENT]` when there is nothing useful to surface.

## Hermes and Codex

SolBeat uses a fresh named Hermes profile called `sol`; it does not copy the default profile's sessions or credentials. `install-hermes` writes a physical executable under that profile because Hermes intentionally rejects cron scripts that resolve outside its own `scripts` directory.

The generated gate pins its compatible Python interpreter, config, state-home,
and actual package import paths, so it does not depend on ambient `PATH` or
`PYTHONPATH` values in either a checkout or an installed package. If the Python
interpreter, config, state-home, or package installation path changes, rerun
`install-hermes` and update the cron prompt before resuming the job; this avoids
selecting an older system Python, splitting gate/ack across two databases, or
using a stale import path.

The project-local Codex policy in [`.codex/config.toml`](.codex/config.toml) forces unattended turns into workspace-write mode, adds only the SolBeat state directory as an extra writable root, and disables network access. It sets no approval policy, so whoever runs Codex here keeps their own; the unattended runner sets `approval_policy = "never"` in its own Codex profile. The cron prompt in [`config/cron-prompt.md`](config/cron-prompt.md) keeps event data separate from instructions and requires an explicit ack or release.

## WhatsApp boundary

Pairing is deliberately human-only because its QR code and phone identifiers are credentials/account data. After the isolated profile and safe bridge port have been prepared, run this yourself in a private terminal:

```bash
sol whatsapp
```

Use WhatsApp → Settings → Linked Devices → Link a Device. Choose a dedicated bot number and enter an explicit allowlist in the private wizard; do not choose `*`. The wizard only enables WhatsApp after a pairing credential actually exists.

After pairing:

1. Run `sol gateway restart` in the private terminal.
2. Message the bot from the intended private chat and send `/sethome`.
3. Wait for the gateway's success reply, then tell the local operator that pairing and `/sethome` are complete.

`/sethome` records the chat locally, so no phone number or home-channel identifier needs to be pasted into an agent chat. Keep the QR, numbers, identifiers, and bridge logs private. SolBeat delivery remains `local` until the cron job is deliberately switched to `whatsapp`; pairing alone sends no proactive message.

The selected local bridge port is `18742`, not Baileys' default `3000`, because another local service owns port `3000` on this device.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The test suite covers silent ticks, dedupe, redaction (including event labels),
strict configuration, quiet/urgent policy, rate limiting, concurrent leases,
expiry/backoff, dead-letter retention, indefinite and timed pause/resume,
gentle check-ins, CLI output, package-local defaults, and physical Hermes shim
installation.

The v0.1 live deployment uses the source-tree wrapper and its explicit device
configuration. Wheel metadata includes the neutral default so an isolated
installed `solbeat gate` works without the repository checkout; installation
does not configure Hermes, a scheduler, delivery, or the device-specific daily
pulse.
