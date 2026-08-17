# Pending correction to mapd issue 129

**Status: NOT YET POSTED.** GitHub returned 503 on both the REST and GraphQL APIs across four
attempts on 2026-08-17. The text below is ready to go verbatim; post it as a comment on
`pfeiferj/mapd` issue 129 when the API is back:

```bash
gh issue comment 129 --repo pfeiferj/mapd --body-file bluepilot/MAPD-ISSUE-129-CORRECTION.md
```

(Strip everything above the divider first, or paste the block by hand.)

## Why it exists

The issue was filed with a coverage number measured on **one road**, US-6, and the framing implied
the tag was generally available. Checking a second road the same day contradicted it:

    US-6    134 of  497 ways   27%
    I-15     17 of  400 ways    4%
    US-89     0 of 1594 ways    0%

US-89 is a comparable Utah highway with its own 2+1 sections and carries `change` on nothing at all.
So the tag is one corridor, not a property of US roads, and the 86% multi-lane split in the original
post describes that single corridor.

**The lesson, which is the reason this file exists rather than just a comment:** the tag census in
`MAPD-V2-PLAN.md` was thorough about WHICH TAGS were checked and silent about HOW MANY ROADS each
number came from. Every percentage in that document should be read as "on the roads sampled", and
any number quoted outward needs at least two independent roads behind it first. Enumerating the
tags exhaustively while sampling a single way set is not a measurement of coverage; it is a
measurement of US-6.

---

Correcting my own numbers before you spend any time on this.

I measured one road. Checking a second changes the picture a lot:

```
US-6    134 of  497 ways   27%
I-15     17 of  400 ways    4%
US-89     0 of 1594 ways    0%
```

US-89 is a comparable road to US-6 in the same state, also with 2+1 passing sections, and it carries
the tag on nothing at all. So `change` coverage here is one corridor rather than a general property
of US roads, and the 86% multi-lane split in my original post describes that single corridor.

That is weaker than I made it sound, and you should weigh it accordingly. The argument that stands is
the one about what the tag means when it is present: `change=no` across two same-direction lanes is a
restriction nothing on the vehicle can perceive, and no other tag carries it. The argument that does
not stand is that it is widely available.

Entirely reasonable to close this as not worth the effort. I would rather you had the real number
than my first one.
