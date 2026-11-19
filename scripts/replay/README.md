# Replay test for chat changes

Runs real past Telegram messages through `hermes chat -q` on the VPS inside a private mount
namespace, so a change to a skill, the toolsets or the model can be compared with a baseline
before it goes live. Built 2026-11-18 for the chat-cost work.

- `replay_setup.sh` (as `hermes`): copies the vault, `~/.hermes` state and the repo skills to
  `/tmp/hreplay/pristine`, dropping Telegram and email credentials.
- `replay_ns.sh` (as root): runs one command with the live vault, `~/.hermes` and repo skills
  replaced by the sandbox copies. Nothing inside can reach the live files.
- `export_cases.py` (as `hermes`): writes the chosen messages (by row id in `state.db`) to
  `/tmp/hreplay/cases/`. Edit the list for new cases.
- `replay_run.py <variant> [case-prefix…]` (as root): resets the sandbox per case, applies
  `variants/<variant>/apply.sh` and `variants/<variant>/toolsets` if present, runs each turn and
  records tokens, steps, cost and the meals written. `compare.py A B` prints two variants side by side.

Costs real API money: about $2 for the seven text cases on Sonnet 4.6. Ask before running.
Limits: photo messages cannot be replayed (the command line attaches one image at most), and the
background review does not run in one-shot mode. Delete `/tmp/hreplay/pristine` and `work` after
use: they hold a copy of the vault and of the API key.
