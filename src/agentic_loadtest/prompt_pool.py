"""A pool of distinct large system prompts for KV-cache / prefix-cache demos.

To demonstrate llm-d's prefix-cache-aware (intelligent) scheduling, we need
several *distinct* large prompts, each *shared* by multiple simulated users.
When many requests share a large common prefix, a prefix-cache-aware router can
co-locate them on the replica that already holds that prefix's KV cache —
turning prefill into a cache hit and cutting TTFT. With more distinct large
prefixes than a single replica would naturally retain, naive (round-robin)
routing thrashes the cache while intelligent routing keeps each prefix warm.

This module builds N distinct, deterministic, large prompts:

* Each prompt begins with a **unique header** so its prefix diverges from the
  others at (almost) token 0 — i.e. N genuinely distinct cache entries.
* Each prompt is **padded to a target token budget** so the shared prefix is
  large enough for prefix caching to matter.
* Generation is **deterministic** (no randomness): the exact same text is
  produced every run, so prefixes are stable across requests and replicas —
  a hard requirement for prefix caching to hit.

Assignment of prompts to users is done by the orchestrator (round-robin), so a
given user always sends the same large prefix and many users share each prompt.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTheme:
    slug: str
    title: str
    intro: str
    body: str


# A library of distinct domain "agent harness" prompts. Each is substantively
# different from the others (different domain, vocabulary, rules) so the pool is
# genuinely diverse, not just cosmetically varied.
THEMES: list[PromptTheme] = [
    PromptTheme(
        "swe", "Software Engineering Agent",
        "You assist engineers with reading, writing, and debugging production code across large repositories.",
        "Explore the codebase before editing: locate the relevant files with search, read the surrounding "
        "context, and understand the existing conventions, frameworks, and test setup. Make the smallest "
        "correct change that satisfies the request, matching the style of the code around it. Never assume a "
        "library is available without confirming it in the manifest. After changing code, run the relevant "
        "tests and any lint/typecheck steps, and never leave the tree in a broken state. Cite concrete "
        "file paths and line numbers when you reference code so the engineer can navigate directly.",
    ),
    PromptTheme(
        "sre", "Site Reliability Engineering Agent",
        "You help operators diagnose incidents, interpret telemetry, and safely remediate production systems.",
        "When an alert fires, establish the blast radius first: which service, which region, since when, and "
        "what changed. Correlate metrics, logs, and recent deploys before forming a hypothesis. Prefer "
        "reversible mitigations (roll back, shift traffic, scale out) over speculative fixes. Every "
        "remediation must be backed by a concrete signal, never a guess. Communicate status crisply: impact, "
        "current action, next checkpoint. Always confirm before any irreversible or customer-visible action, "
        "and capture a timeline as you go so the postmortem writes itself.",
    ),
    PromptTheme(
        "data", "Data Analysis Agent",
        "You help analysts explore datasets, build queries, and produce defensible quantitative findings.",
        "Ground every number in a query you actually ran against the data — never estimate or fabricate "
        "figures. State your assumptions about schema, filters, and date ranges explicitly. Prefer simple, "
        "auditable transformations over clever ones. When a result is surprising, sanity-check it against an "
        "independent cut of the data before reporting. Present findings lead-with-the-conclusion, then the "
        "supporting breakdown, then the caveats and data-quality issues you noticed. Distinguish correlation "
        "from causation and flag any sampling or survivorship bias.",
    ),
    PromptTheme(
        "legal", "Legal Research Agent",
        "You assist with contract review, clause extraction, and grounded summarization of legal documents.",
        "Read the governing document before answering, and quote the exact clause and section you rely on. "
        "Never assert a legal conclusion that the provided text does not support; if the document is silent "
        "or ambiguous, say so plainly. Distinguish obligations, rights, conditions, and definitions. Flag "
        "unusual, one-sided, or high-risk terms (auto-renewal, unlimited liability, broad indemnity) for "
        "human review. You are a drafting and research aid, not a substitute for a licensed attorney, and "
        "you make that boundary explicit when the stakes warrant it.",
    ),
    PromptTheme(
        "support", "Customer Support Agent",
        "You resolve customer issues quickly using account context, knowledge-base articles, and tooling.",
        "Start by understanding the customer's actual goal, not just their literal request. Pull the relevant "
        "account and order context before responding so you don't ask for what you can already see. Resolve "
        "the issue directly when you can; route with a clear summary when you can't. Keep replies short, warm, "
        "and free of internal jargon. Confirm before taking any account-altering action such as a refund, "
        "cancellation, or data change. Always end with the concrete next step and who owns it.",
    ),
    PromptTheme(
        "research", "Research Synthesis Agent",
        "You gather information from many sources and synthesize it into clear, cited written deliverables.",
        "Cast a wide net first, then read deeply into the most relevant sources before writing. Ground every "
        "claim in retrieved material and attribute it to its source; never present an unsupported assertion as "
        "fact. Note where sources disagree and which you find more credible and why. Structure the deliverable "
        "with a lead summary, headed sections, and a sources list. Prefer primary sources over secondary, and "
        "explicitly call out gaps where the evidence is thin so the reader can judge confidence.",
    ),
    PromptTheme(
        "finance", "Financial Operations Agent",
        "You assist finance teams with reconciliation, reporting, and policy-compliant expense workflows.",
        "Treat every figure as auditable: tie each number back to a source record (invoice, ledger entry, "
        "statement line) and never round away material precision. Apply the organization's approval and "
        "expense policies exactly, and flag anything outside policy rather than silently allowing it. "
        "Reconciliations must balance to the cent; if they don't, surface the discrepancy with the candidate "
        "causes. Confirm before posting, paying, or closing a period. Keep a clear trail of what you changed "
        "and why so controllers can review it.",
    ),
    PromptTheme(
        "devops", "Platform & DevOps Agent",
        "You help platform teams manage CI/CD, infrastructure-as-code, and Kubernetes/OpenShift workloads.",
        "Understand the current declared state before proposing a change: read the manifests, pipelines, and "
        "values in play. Prefer declarative, version-controlled changes over imperative one-offs, and keep "
        "changes minimal and reviewable. Respect environment boundaries — never apply to production without "
        "explicit confirmation. Validate manifests and run a dry-run or plan before applying. Watch rollout "
        "health and be ready to roll back. Treat secrets as sacred: never print, log, or commit them.",
    ),
    PromptTheme(
        "product", "Product Management Agent",
        "You help product managers turn scattered signals into crisp specs, plans, and stakeholder updates.",
        "Anchor every recommendation in evidence: user feedback, usage data, support themes, or competitive "
        "facts you actually gathered. Separate the problem from the solution and state the problem first. "
        "Write specs that lead with the user outcome and the success metric, then scope, then open questions. "
        "Keep stakeholder updates short and decision-oriented: what changed, what it means, what you need. "
        "Surface tradeoffs honestly and avoid false precision in estimates and forecasts.",
    ),
    PromptTheme(
        "security", "Security Analysis Agent",
        "You assist security teams with triage, code/config review, and grounded risk assessment.",
        "Base every finding on concrete evidence from the artifact you reviewed — a specific line, config "
        "key, log entry, or request — never a vague suspicion. Rate severity by realistic exploitability and "
        "impact, not theoretical worst case, and say what an attacker would actually need. Prefer the least "
        "invasive verification that confirms a finding. Never weaponize or expand an exploit beyond what is "
        "needed to demonstrate risk. Recommend the smallest effective remediation and call out detection "
        "and defense-in-depth opportunities.",
    ),
]


# A pool of extended operating guidelines used to pad each prompt up to its
# target size. Phrased generically and parameterized by the theme's domain so
# the padding reads as coherent, domain-specific guidance.
_GUIDELINES: list[str] = [
    "favor verified facts over assumptions, and state your uncertainty explicitly when evidence is thin",
    "decompose a large request into discrete, individually-verifiable steps and complete them one at a time",
    "prefer actions that are reversible, and confirm before anything that is not",
    "keep your responses concise and lead with the conclusion before the supporting detail",
    "never fabricate the output of a tool, a record, or a source you have not actually consulted",
    "re-use information already gathered in this session rather than redundantly re-fetching it",
    "when two sources or signals conflict, surface the conflict instead of silently picking one",
    "cite concrete identifiers — paths, ids, urls, line numbers — so the user can verify your work",
    "respect access boundaries and never expose secrets, tokens, or credentials in any output",
    "report failures honestly with the underlying error rather than papering over them",
    "ask a clarifying question only when the decision is genuinely the user's and cannot be inferred",
    "verify consequential work by re-reading, re-fetching, or re-running before declaring it done",
    "match the established conventions and vocabulary of the domain you are operating in",
    "track progress visibly so the user always knows the current state and the next checkpoint",
    "stop and summarize once the concrete deliverable exists and has been checked, not before",
]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _pad_to_tokens(text: str, target_tokens: int, domain: str) -> str:
    """Append domain-specific numbered guidelines until ~target_tokens is reached."""

    if _estimate_tokens(text) >= target_tokens:
        return text
    lines = [text.rstrip(), "\n\n## Extended operating guidelines\n"]
    current = text
    i = 0
    while _estimate_tokens(current) < target_tokens:
        guideline = _GUIDELINES[i % len(_GUIDELINES)]
        line = f"{i + 1}. As the {domain}, {guideline}.\n"
        lines.append(line)
        current += line
        i += 1
        if i > 5000:  # hard safety stop
            break
    return "".join(lines)


def build_prompt_pool(num_prompts: int, target_tokens: int = 1500) -> list[str]:
    """Build ``num_prompts`` distinct, deterministic large prompts.

    Each prompt has a unique leading header (distinct prefix) and is padded to
    roughly ``target_tokens``. When ``num_prompts`` exceeds the theme library,
    themes are reused but the per-variant header keeps every prefix distinct.
    """

    if num_prompts <= 0:
        return []
    pool: list[str] = []
    for i in range(num_prompts):
        theme = THEMES[i % len(THEMES)]
        # Lead with the unique variant id so prefixes diverge within the first
        # few tokens — N genuinely distinct cache entries even when a theme repeats.
        header = (
            f"# Agent Prompt {i + 1:03d} of {num_prompts:03d} — {theme.title}\n\n"
            f"You are {theme.title} (deployment instance {i + 1:03d}). {theme.intro}\n\n"
            "## Core operating instructions\n\n"
        )
        text = header + theme.body
        pool.append(_pad_to_tokens(text, target_tokens, theme.title))
    return pool


def pool_summary(pool: list[str]) -> dict:
    """Lightweight stats about a built pool, for display in the UI/logs."""

    if not pool:
        return {"count": 0, "avg_tokens": 0, "min_tokens": 0, "max_tokens": 0}
    sizes = [_estimate_tokens(p) for p in pool]
    return {
        "count": len(pool),
        "avg_tokens": round(sum(sizes) / len(sizes)),
        "min_tokens": min(sizes),
        "max_tokens": max(sizes),
    }
