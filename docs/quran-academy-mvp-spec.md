# Quran Academy Platform — MVP Spec (v2)

**The problem:** your time is the bottleneck. Students span timezones and skill levels, sub-teachers exist but need routing and quality control, some parents want to negotiate price, and some parents want you specifically because of a past relationship. This spec covers how the platform handles all of that without you manually triaging every request.

---

## 1. Roles

| Role | Access |
|---|---|
| **Lead teacher (you)** | Sets your own capacity, approves sub-teachers, approves pricing exceptions, views rubric scores academy-wide, edits curriculum/levels |
| **Sub-teacher** | Own schedule/capacity, teaches assigned students, scores sessions against the shared rubric, sees own payout statement |
| **Student (adult)** | Books sessions, sees own progress, submits recitation samples |
| **Student (minor)** | Same as above, simplified UI, no billing/account access |
| **Parent** | Linked read-only view of one or more children: attendance, rubric scores, teacher notes, negotiated rate if applicable |

Age at signup determines whether the account activates standalone or requires a linked parent account first.

---

## 2. The routing engine — solving the capacity bottleneck

This is the core mechanic. When a student requests a session, the system decides who teaches it — you don't decide manually each time.

**Order of resolution:**
1. **Cohort first, if the level allows it.** Beginner levels (Qaida, basic Arabic) run as small group classes. One teacher covering 4–6 students at once is the single biggest lever for increasing your throughput without hiring anyone.
2. **You, if you have capacity.** You set a weekly capacity limit; the system stops offering your 1:1 slots once you're full instead of you declining requests one by one.
3. **A matched sub-teacher, if you're full.** Matched by specialty, timezone overlap, and remaining capacity.
4. **A waitlist entry, if the parent specifically requested you and you're full** — see section 4 below. This is the one case where the system does *not* auto-route to a sub-teacher.

## 3. Negotiated pricing — handling "some pay more, some pay less"

Every student gets a **pricing record** rather than one global rate. There's a standard rate per level, and any deviation from it is logged as an explicit exception rather than lived only in your memory or a WhatsApp thread.

Each exception records: the standard rate, the actual agreed rate, a reason (financial hardship, premium for choosing you directly, sibling discount, or "standard — no negotiation"), who approved it, and a private note on what was actually agreed.

Two things this protects:
- **Sub-teacher payouts stay independent of family pricing.** If a family pays less because you agreed to a discount, the sub-teacher teaching that student still gets their normal rate — your margin absorbs the difference, not the teacher's income. This distinction matters and should never blur.
- **Approval stays with you.** Only you can create a pricing exception. It's the one lever that affects your margin directly, so it shouldn't be something a well-meaning sub-teacher can grant on their own.

## 4. "I want you specifically" — preferred-teacher requests

The routing engine defaults to auto-assignment, but a parent can name you directly at booking time. This needs to be handled honestly, not silently overridden:

- If you have capacity → books directly with you, no routing logic applied.
- If you're full → the request goes to a **named waitlist** for you specifically, not auto-routed to a sub-teacher. The parent is told plainly: "you're on the waitlist for [you], here's roughly when a slot opens" — not quietly redirected to someone they didn't ask for.
- When a slot frees up (a cancellation, or you raise your weekly capacity), the waitlist is offered the opening — priority can be influenced by how long they've waited, whether they're a returning family, or whether a pricing premium was agreed as part of getting priority access. That's a business decision worth making deliberately rather than defaulting.

The underlying principle for both sections 3 and 4: **exceptions are tracked, not silent.** Anything that deviates from the default (a different price, a specific teacher override) gets recorded with a reason, so your data stays honest and auditable six months from now.

## 5. Assessment — what makes delegation to sub-teachers safe

A shared rubric per track (Tajweed, Hifz, Arabic, Qaida) with a handful of criteria — makhraj accuracy, madd rules, fluency, waqf, etc. Every teacher, lead or sub, scores against the *same* criteria after each session. This is what lets you delegate without losing quality control: instead of trusting each sub-teacher's personal judgment, you get a dashboard of average scores by teacher, and sub-teachers can flag a session for you to personally review if something seems off.

Scores roll up into a periodic progress snapshot — this is what parents and students actually see: attendance, rubric averages over time, and a short teacher summary.

## 6. Live sessions — video via Jitsi

Jitsi Meet embeds directly into your platform (web or app) rather than sending students to an external tab like Google Meet requires — important for younger students and for keeping the experience feeling like your academy, not a hand-off to another company.

- Start on the free tier (currently free up to 25 monthly active users on the managed 8x8 hosting, or self-host for no per-user cost at all if you're comfortable running a small server).
- Each booking gets its own unique room, generated automatically when the session is created — no manual link-sharing needed.
- Session start/end can be tracked automatically, which feeds directly into attendance records and, eventually, payout calculations for sub-teachers.
- Move to self-hosting or a paid tier only once you outgrow the free cap — no need to decide that now.

## 7. Payouts

Sub-teacher payouts are calculated from actual completed sessions × their individual payout rate (set by you, independent of what the family was charged). Generate statements on a regular cadence (weekly or monthly) rather than reconciling manually — this becomes worth automating once you have more than one or two sub-teachers active.

## 8. Onboarding flow (student side)

1. Sign up → age check → parent account linked if a minor
2. Short placement: student records a brief recitation sample (or marks themselves complete beginner)
3. You or a senior teacher reviews the sample asynchronously and sets a recommended starting level
4. Student picks timezone-friendly slots → routing engine assigns cohort/teacher automatically, unless they've named you specifically
5. First session happens; rubric scoring starts immediately so progress tracking begins from day one

## 9. Build order

1. **Accounts, roles, parent linking** — foundation everything else depends on
2. **Curriculum + placement** — tracks, levels, placement review
3. **Basic scheduling + booking + Jitsi video** — get sessions actually happening before adding intelligence
4. **Routing engine** — capacity-based auto-assignment, cohort-first logic, the actual bottleneck-solver
5. **Pricing exceptions + preferred-teacher waitlist** — layer these in once basic booking works, since both are refinements on top of routing rather than prerequisites for it
6. **Assessment rubric + progress snapshots** — unlocks safe delegation to sub-teachers
7. **Payouts** — automate once you have more than one active sub-teacher

Building the routing intelligence, pricing exceptions, and waitlist logic before you have real students and real demand patterns is premature — get sessions happening manually first, then automate based on what actually comes up.
