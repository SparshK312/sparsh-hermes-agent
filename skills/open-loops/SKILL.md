---
name: open-loops
description: 'Track, update and close the OPEN LOOPS Hermes follows up on for Sparsh: things he is waiting on from someone, steps he owes, and plans with a condition ("if nothing by Friday, escalate"). Use when he answers one of your follow-ups ("yeah she replied", "still nothing", "done"), when he tells you about something to keep an eye on ("remind me to check on X", "I am waiting on Y for Z", "if they have not answered by Monday I will call"), or asks what is open ("what am I waiting on", "what is on my list", "open loops").'
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [assistant, follow-up, tracking]
    category: productivity
---

# open-loops

Hermes keeps a list of unfinished threads and checks on them. You change that list ONLY with the `loops.py` command, run through `terminal`. The file `00 - Dashboard/Open Loops.md` in the vault is a machine-written view of it: reading it is fine, editing it is blocked and would change nothing.

```
L="/usr/bin/python3 /home/hermes/.hermes/scripts/assistant/loops.py"
$L list                      # what is open (ids, who, next step, when it is looked at again)
$L add --title "<short name>" --kind waiting_on|my_move|conditional_plan [--who "<person>"] [--next-step "<what happens next>"] [--due YYYY-MM-DD] [--check-after YYYY-MM-DD]
$L update <id or distinctive fragment> [--next-step "…"] [--due YYYY-MM-DD] [--check-after YYYY-MM-DD] [--note "…"]
$L snooze <id or fragment> --until YYYY-MM-DD
$L close  <id or fragment> --evidence "<what finished it, in his words, with the date>"
```

The date is GIVEN in the turn context. Use it; never run `date`.

## When he answers one of your follow-ups

The turn may carry an "Assistant background" block listing follow-ups you sent that he had not answered, each with a `[loop:<id>]` tag. If his message is a reply to one:

- It happened / it is settled ("she replied", "got it", "signed", "done") → `close <id> --evidence "<his words> (<today's date>)"`. Then answer in one line.
- Still waiting ("nothing yet", "not yet") → `snooze <id> --until <a sensible date, usually 2 days out, or the date he names>`. Reply in one line; do not lecture or re-ask.
- The plan changed ("I'll call them Friday instead") → `update <id> --next-step "…" --check-after <date>`.
- He does not want it tracked ("drop it", "never mind that one") → `close <id> --evidence "he asked to drop it (<date>)"`.

If you cannot tell which loop he means, run `list` and ask ONE short question. An ambiguous fragment is refused by the command with the candidates listed; pick the id from that list, do not retry with rewordings.

## When he tells you about something new to track

Add it only when he states a wait, a step he owes, or a condition. Use his words for the title and the next step; never invent a date. Kinds:

- `waiting_on`: someone else has to do or send something. Put the person or organisation in `--who` exactly as he said it.
- `my_move`: he owes a concrete step by a date.
- `conditional_plan`: "if X has not happened by D, do Y". Put D in `--check-after` and Y in `--next-step`.

Confirm in one line: what you are tracking and when you will look at it again.

## Rules

1. Bookkeeping only. You track and ask. You do not send email, book anything, or change his calendar or the internship board from this skill. If a loop needs an action, say what the next step on file is and let him decide.
2. Never close a loop on a guess. A close needs what he said or what you saw (for mail: sender, subject, date).
3. State what you checked and what you could not. "I searched your inbox for mail from her since Monday: nothing" is fine; "she has not replied" is a claim about the world you cannot make.
4. One question at most per reply.
5. Do not bring up open loops in an unrelated conversation (logging a meal, a quick question). The background block is context, not a to-do list to recite.
