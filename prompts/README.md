# Task prompts

The written instructions for the factory's Claude scheduled tasks that serve both accounts (the social layer). Each
prompt ships here with the part that uses it; the task itself is created at go-live, with Andrew's OK, and points at
its file. `factory.py doctor` reads each installed task's prompt, and the prompts/ file it points to, for rules that
ended (hermes.TASK_PROMPTS, hermes.TASK_PROMPT_FILES) and skips a task that is not installed yet.

| task | prompt | runs | what it writes |
|---|---|---|---|
| clip-coach | prompts/coach.md | `20 5,12 * * *` (05:20 and 12:20, before the sign-off at about 06:00) | each draft's written scorecard (state/<slug>/coach/<day>/<HHMM>.json) |
| clip-weekly-report | prompts/weekly-report.md | `0 17 * * 0` (Sunday 17:00) | Sunday's report (state/<slug>/learn/weekly/YYYY-Www.md) |
| clip-results-coach-auto | prompts/results-coach.md | `5 5 * * *` (about 05:05) | what happened in the best and worst clips (state/<slug>/learn/notes-YYYY-MM-DD.json, shown in the morning file) and at most two proposals an account |
| clip-fixer | prompts/fixer.md | `20 7,16 * * *` (07:20 and 16:20) | the safe fixes (Try again, bench fill, missing coach scorecards, a cut for a clip not signed yet) and `Fixed MM.DD.YYYY.md` on the Desktop with what is left for Andrew |
| politics-news-desk-auto | prompts/news-desk.md | `10 6,8,10,12,15,17 * * *` (06:10 to 17:10) | one Politics news post request a run, five a day at the most (state/politics/cards/news/<day>/<id>.json, through `cards news add`); Andrew signs each on the Cards tab |

The coach's prompt arrives with the coach (clipfactory/coach.py), and the weekly report's and the results coach's
with the learner (clipfactory/learn.py). One truth: a rule stated in a prompt is changed in every file that states it.
