# Job Title Hierarchy Guardrails
### For AI/agent-solutions SaaS — US, NZ, SG, AU

This is a tiered decision-maker framework, not a strict filter list — the point is to give
your LLM analyzer *reasoning structure* rather than an exact-match keyword list, since (as
you noted) Apollo's own title filter misses too many real-world variants to be reliable on
its own.

---

## 1. Why tiers, not a flat list

A flat "include these titles" list breaks in two ways for your use case:

- **Startups compress hierarchy.** A 50-person company's "Head of Product" often *is* the
  final decision-maker — the same title at a 500-person company is an influencer three
  layers below budget authority.
- **Regional naming diverges.** "Managing Director" in AU/NZ/SG frequently means what "CEO"
  means in the US. A flat US-centric list will systematically miss real decision-makers in
  your other three markets.

So the guardrail logic should be: **seniority tier × functional relevance × company-size
adjustment × regional synonym mapping** — not a single keyword match.

---

## 2. Seniority tiers

| Tier | Label | Examples |
|---|---|---|
| 1 | Owner / Founder | Founder, Co-Founder, Managing Partner |
| 2 | C-Suite | CEO, President, CTO, CIO, CPO, COO, Chief AI Officer, Chief Data Officer, Chief Digital Officer |
| 3 | VP / Senior Leadership | VP Engineering, VP Product, VP AI/Data, VP Operations, SVP, EVP |
| 4 | Director | Director of Engineering, Director of Product, Director of AI/Data, Director of Innovation, Director of Technology |
| 5 | Senior Manager / Principal IC | Engineering Manager, Senior Product Manager, Principal Engineer, Lead Engineer, Head of Engineering *(when not a Tier 3/4 equivalent — see §4)* |
| 6 | Manager | Product Manager, Program Manager, Project Manager |
| 7 | Individual Contributor | Analyst, Associate, Specialist, Coordinator, Engineer (non-lead), Intern |

**Default rule:** Tiers 1–6 = "Required." Tier 7 (pure individual-contributor/admin roles)
= excluded by default, unless a specific override applies (see §5).

**Correction from earlier draft:** the presence of a senior contact (C-suite/VP/Director) at
a company is **not** a reason to exclude Manager-tier contacts. They serve different roles
in a buying process — a Director/VP is often the budget owner, a Manager is often the
hands-on evaluator or champion who actually drives the recommendation. Your card rail
should capture both, tagged with a `role_type` so you can see who's who downstream, rather
than filtering managers out because "someone more senior already qualifies."

---

## 3. Functional relevance filter

Seniority alone isn't enough — the *function* has to plausibly own or influence a buying
decision for an AI/agent tooling product. Apply this as an AND condition alongside tier.

**Relevant functions (include):**
Engineering / Software / AI / ML, Product, Data, Innovation / R&D, IT / Digital
Transformation, Operations (when paired with "Technology," "Digital," or "AI" in the
title), General Management / Founder-level roles regardless of stated function.

**Excluded by default, even at senior tiers:**
Sales (unless RevOps/Sales Engineering), Marketing (unless MarTech/Growth-AI), HR/People,
Legal/Compliance, pure Finance (unless CFO or Director of Finance with explicit budget
sign-off language), Customer Support/Success (non-managerial).

**Edge case — Founder/CEO/Managing Director:** always include regardless of stated
function, since at Tier 1–2 the function label is unreliable (a technical co-founder may be
titled "CEO" or vice versa) and they hold cross-functional authority anyway.

---

## 4. Regional title mapping (US vs NZ / SG / AU)

Apollo's filters are tuned to US title conventions, which is exactly where you'll lose real
matches in your other three markets.

| US convention | AU / NZ / SG equivalent | Map to tier |
|---|---|---|
| CEO | Managing Director (MD) | Tier 2 |
| VP of X | Head of X, GM of X | Tier 3 |
| Director of X | Head of X *(smaller orgs)* | Tier 4 |
| Regional VP | Country Manager, Regional Director | Tier 3 |
| — | General Manager (GM) — standalone, business-unit head | Tier 2–3 depending on scope |

**Important nuance:** "Head of X" is ambiguous by design — in a US company it's often
Tier 5 (a senior IC-adjacent role), but in AU/NZ/SG startups it's routinely the functional
head with real budget authority (Tier 3–4). Your analyzer should resolve this ambiguity
using company size and region jointly, not title text alone — see §5.

---

## 5. Company-size adjustment logic

This directly answers your "50 employees vs 100 employees" question — but note the
correction from §2: **company size adjusts confidence and role-type tagging, not
whether a Manager-tier contact gets included at all.** A Tier 5/6 contact at a
150-person company is still tagged `required: true` — the size signal changes whether
they're likely the *decision-maker* or the *evaluator/champion*, which matters for how
you sequence outreach, not whether you capture them.

- **≤ 50 employees:** Flat structure — decision authority typically sits with Founder/CEO
  down through Tier 6. At this size, "Head of Engineering," "Engineering Manager," or even
  a senior "Product Manager" can be the actual buyer or the sole technical evaluator.
  **Tiers 1–6 tagged `required: true`, `role_type: decision_maker` for most of them since
  there's rarely enough hierarchy to separate the roles.**

- **51–150 employees:** Hierarchy has formed, but Manager-tier contacts remain highly
  relevant — they're frequently the ones actually running the evaluation even when a
  Director/VP holds final sign-off. **Tiers 1–4 tagged `role_type: decision_maker`.
  Tiers 5–6 tagged `role_type: evaluator`. Both are `required: true`.**

- **150+ employees:** Layered hierarchy is more reliable, so the decision-maker/evaluator
  split is more meaningful here. **Tiers 1–4 → `role_type: decision_maker`. Tiers 5–6 →
  `role_type: evaluator`. Both remain `required: true`** — a Manager at a 500-person
  company is still worth having in your list; they're just not the one who signs the
  contract, so your outreach sequencing (e.g., champion-first vs. exec-first) can use the
  tag to decide who to approach and how.

The net effect: **no company-size band should ever cause a Manager-tier contact to be
dropped.** Size only changes the `role_type` label attached to them.

---

## 6. Function-override keywords (confidence booster, not a gate)

Previously this section decided *whether* a Tier 5 title got included. That's no longer
the right job for it, since Tier 5–6 are now included by default. Instead, use these
keywords to boost `confidence` and reinforce `role_type: decision_maker` even at a
Manager-tier title, since they signal direct ownership of the exact problem space you
sell into:

`AI`, `Agent`, `Agentic`, `Automation`, `ML`, `Machine Learning`, `Innovation`,
`Applied AI`, `AI/ML`, `Intelligent Automation`, `Digital Transformation`

Example: "Engineering Manager, Applied AI" at a 200-person company → `required: true`,
`role_type: decision_maker` (upgraded from the default `evaluator` tag for that size band),
`confidence: high` — because the keyword match indicates they likely own this initiative
directly, not just execute on it.

---

## 7. Suggested prompt structure for your LLM analyzer

Feed the model these four inputs per contact and have it reason through the tiers rather
than pattern-match a fixed list:

```
job_title: <raw string>
company_name: <string>
apollo_id: <string>
employee_count: <int, if known>
region: <US | NZ | SG | AU>
```

Ask it to output:
```
tier: 1–7
function_relevant: true/false
regional_synonym_applied: <mapping used, or null>
role_type: decision_maker | evaluator
required: true/false
confidence: high/medium/low
reason: <one line>
```

Note: `required` should be `true` for Tiers 1–6 whenever `function_relevant` is true —
company size and keyword matches inform `role_type` and `confidence`, not the `required`
boolean itself. Only Tier 7 (or a function mismatch) should push `required` to `false`.

This keeps the reasoning auditable — when you're reviewing why something got tagged
"Required," you can see which rule (tier, function, region mapping, size adjustment,
keyword override) actually fired, instead of a black-box yes/no.

---

## 8. Explicit exclude list (Tier 7 only — always, regardless of size/region)

Analyst, Associate, Specialist, Coordinator, Intern, Graduate/Junior *, Executive
Assistant, Recruiter, Sales Development Rep, Account Executive *(unless Sales
Engineering/RevOps)*, HR Business Partner, Talent Acquisition *, Marketing Coordinator,
Customer Support Rep *(non-managerial)*.

These stay excluded even under the ≤50-employee relaxed rule in §5, since they represent
functions/seniority levels that don't plausibly evaluate or influence a B2B SaaS purchase
at any company size.
