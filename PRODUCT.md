# PRODUCTS.md

Four questions, answered against what this build actually does — not what it could do.

## 1. Who loses the most when a question is broken, and how the design reflects it

The student who reported it, and by more than anyone else in the loop. They studied, got marked
wrong by a wrong key, and the only thing they have is a report button — so if the platform
answers with a canned "no error found", they stop reporting and the platform loses its cheapest
detector of bad content.

That is why the solver never sees the key, the stored solution or the student's comment: the
comparison in stage 3 is only worth something if the solve wasn't anchored on the thing it's
checking. It's also why the reply is written per report rather than templated, and why it cannot
say "fixed" until the faculty has actually applied it — the student is told what's true at the
moment they read it, including "still being checked".

## 2. What I would never let the AI auto-apply, and why

Nothing that adds, removes or rewrites an option, changes the question type, or touches a
figure — each of those silently changes what the question is asking, and it can't be undone by a
student who already saw it. No edit whose `find` string isn't unique in the field either, because
it lands in the wrong place.

A student's report alone never moves a key. `answer_key` auto-applies only when the independent
solve and the stored solution agree with each other and contradict the key (R001, R003, R004,
R005); everything else goes to faculty, and 21 of 26 defects in this data carry no structured
edit for exactly that reason.

## 3. One metric I'd watch in week 1

The faculty apply rate on cases the pipeline routed to auto-apply — approved unchanged ÷ flagged.
It's the only number that tests the triage itself rather than the model's fluency: a low value
means the confidence threshold is wrong, not that the faculty is slow. Volume in week 1 will be
small, so I'd read it as a direction, not a score.

## 4. One idea beyond this brief

Point the same blind solve at the whole question bank, nightly, instead of only at reported
questions. Most broken content is never reported — the student assumes they're wrong and moves
on. It's the same solver and the same classifier, with the report as the trigger replaced by a
schedule, so defects get found before someone loses marks to them.
