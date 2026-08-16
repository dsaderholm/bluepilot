# What other self-driving systems give a model, and what they keep as code

Researched 2026-08-16 at his request, to back the CLAUDE.md rule **"THE MODEL GETS WHAT HE HAS NO
PREFERENCE ABOUT. WRITTEN CODE GETS THE REST."** That rule was arrived at from first principles in
conversation; this is the check against what the industry actually does, and it mostly holds — with
one company betting hard against it.

Everything below is from sources linked at the bottom, not recollection. Two claims were got wrong
earlier the same day by inferring from a name, so quotes are quoted.

## Tesla — the maximal end-to-end position

FSD v12 replaced **over 300,000 lines of explicit C++** with a single end-to-end network trained on
millions of video clips. Raw camera in, steering and pedals out.

The bet is that hand-written policy is a **bottleneck**: every rule a human writes is a rule that
does not generalize, and human demonstration data contains the policy already. If that bet pays off,
most of what this fork writes ages badly — see the caveat at the bottom for the part that does not.

## Waymo — the opposite conclusion, from more autonomous miles than anyone

They built the end-to-end version and did not ship it. **EMMA** maps raw sensor data directly to
planner trajectories, perception objects and road graph elements — and it is **research only**. It
does not drive their cars. Commercial deployment stayed **modular, three modules**.

Co-CEO Dmitri Dolgov on the monolithic architecture:

> "makes it very easy to get started, but it's wildly inadequate to go to full autonomy safely and
> at scale"

Their 2025 architecture is explicitly hybrid — "think fast, think slow": a fast fused-sensor
reactive path (System 1) plus a slower deliberative one (System 2).

**This is the single most useful data point in the survey**, because it is not a company that lacks
the ability to go end-to-end declining to; it is one that built it, evaluated it, and kept the
modular stack for the cars that carry passengers.

## Mobileye — the rule, formalized and standardized

The closest thing in the industry to what CLAUDE.md states informally. **RSS
(Responsibility-Sensitive Safety)** is a formal mathematical model: five rules, with analytically
checkable safe-distance conditions in both the longitudinal and lateral dimensions. It is written
code, it is **verifiable**, and it sits **over** the machine-learned perception — operating on
*assumed bounds on perception error* rather than trusting perception directly.

So Mobileye's answer is: the model says what is out there within a stated error bound, and **formal
written rules decide what is safe to do about it.** RSS encodes "duty of care", which is a legal
concept rather than a perception problem — you cannot learn it from a fleet, because it is a value,
not a pattern.

## comma — and this part is already on his car

The driving model is end-to-end for the path. Written code still holds the controllers and,
critically, **panda**: separate C code that enforces torque and acceleration limits **regardless of
what the model asks for**. This repo touches that layer directly — `ford.h`'s `Steering_Data_FD1`
tx_hook checks only cancel and resume, which is why ICBM's gap presses go out regardless of
`controls_allowed`.

So his car already runs the industry-standard shape: **a learned path, inside a written envelope.**
What this fork adds is a third layer — written *policy* — between them.

---

## The pattern everyone converged on

The split is not quite perception-versus-policy. It is **capability versus guarantee**:

| | goes to |
|---|---|
| infinite variety — what the world looks like, what that car will do, what path a human would take | **a model**, because it cannot be enumerated |
| anything that must be **proved, audited, or pointed to after an incident** — following distance, right of way, torque limits | **written code** |

**Nobody ships without a written layer, including Tesla.** And the written layer is consistently
where the VALUES live, because values are not patterns in data.

## Where this fork differs from all of them

Every system above encodes **society's** preferences: what is safe, who is at fault, duty of care.
This fork encodes **his** — keep right, do not pass uphill when the engine is stressed, be fussier
when not making time, do not pass in a bend this PSCM cannot take.

Same architecture, different content. And it is only possible BECAUSE the policy layer is separable
at all. If policy dissolves into the model, a fleet-trained system hands you the median driver's
preferences with no seam to reach into — which is exactly his objection to BlueCruise refusing to
move into a faster lane, arriving from a different direction.

## What would invalidate this, stated honestly

If Tesla's bet pays off — if a big enough model trained on enough human driving simply *is* good
policy — then categories 1 and 2 of the CLAUDE.md rule collapse into the model and most hand-written
gates here become dead weight.

**Category 3 survives any of these futures.** No amount of fleet training teaches a model that THIS
PSCM needs the car slowed before it will accept a hard steering command, or that this car moves its
set speed 1 mph on a tap and 5 on a hold. There is one such car. There is no fleet.

## Sources

- Tesla FSD v12, 300k lines replaced — https://www.fredpope.com/blog/machine-learning/tesla-fsd-12
- Mobileye RSS — https://www.mobileye.com/technology/responsibility-sensitive-safety/
- Waymo EMMA — https://waymo.com/research/emma/
- Waymo, Demonstrably Safe AI — https://waymo.com/blog/2025/12/demonstrably-safe-ai-for-autonomous-driving/
- Waymo vs Tesla architectures, incl. the Dolgov quote —
  https://www.understandingai.org/p/waymo-and-teslas-self-driving-systems
