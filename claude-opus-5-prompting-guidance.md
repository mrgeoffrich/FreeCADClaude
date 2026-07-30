# Claude Opus 5 — Consolidated Prompting Guidance

Compiled 30 July 2026 from Anthropic's official documentation and blog posts. Explanatory text is summarised; the sample prompt blocks are reproduced as-published since they're intended as copy-paste templates.

**Primary sources**

| Source | URL |
|---|---|
| Prompting Claude Opus 5 (docs) | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 |
| Prompting best practices (docs, all current models) | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices |
| What's new in Claude Opus 5 (docs) | https://platform.claude.com/docs/en/about-claude/models/whats-new-opus-5 |
| Effort (docs) | https://platform.claude.com/docs/en/build-with-claude/effort |
| Migration guide (docs) | https://platform.claude.com/docs/en/about-claude/models/migration-guide |
| The new rules of context engineering for Claude 5 generation models (blog, Thariq Shihipar, 24 Jul 2026) | https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models |
| A field guide to Claude Fable 5: finding your unknowns (blog, 6 Jul 2026) | https://claude.com/blog/a-field-guide-to-claude-fable-finding-your-unknowns |
| Introducing Claude Opus 5 (news, 24 Jul 2026) | https://www.anthropic.com/news/claude-opus-5 |

---

## 1. The short version

Opus 5 performs well out of the box on existing Opus 4.8 prompts. Most of the tuning work is **deletion**, not addition:

1. **Delete verification instructions.** Opus 5 verifies its own work; "double-check your answer" / "include a final verification step" / "use a subagent to verify" cause over-verification and burn tokens with no quality gain.
2. **Prompt for length explicitly.** Default responses and written deliverables are longer than prior Opus models, and effort does *not* reliably shorten visible output.
3. **Re-run an effort sweep.** Effort settings carried over from Opus 4.8 are probably wrong. Start at `high` (the default); `low`/`medium` are now genuinely usable as your primary cost/latency control.
4. **Constrain scope for narrow tasks.** The model can widen scope and apply its own judgment about what the task should be.
5. **Cap or steer subagent delegation.** It delegates more readily than earlier models.
6. **Give the complete task specification up front and let it run.** It completes full tasks rather than leaving stubs.
7. **Keep thinking enabled.** Disabling it produces occasional output artifacts; lower the effort instead.

---

## 2. Model facts that affect prompting

| Property | Value |
|---|---|
| API model ID | `claude-opus-5` (fixed, no date suffix) |
| Context window | 1M tokens — both default and maximum, no beta header, no long-context premium |
| Max output | 128k tokens |
| Thinking | **On by default** (adaptive). `thinking: {"type": "adaptive"}` is valid and equivalent to omitting the field |
| Disabling thinking | Allowed only at effort `high` or below; `xhigh`/`max` + disabled → 400 error |
| Effort levels | `low`, `medium`, `high` (default), `xhigh`, `max` |
| Pricing | $5 / $25 per million input / output tokens (unchanged from Opus 4.8) |
| Prompt cache minimum | 512 tokens (down from 1,024) |
| Not available | Web fetch tool; Priority Tier |
| New | Mid-conversation tool changes (beta header `mid-conversation-tool-changes-2026-07-01`), `fallbacks: "default"` mode (beta header `server-side-fallback-2026-07-01`), Fast mode (research preview, $10/$50 per Mtok) |

`max_tokens` is a hard limit on **total** output — thinking plus response text. Revisit it for any workload that previously ran with thinking off.

---

## 3. Effort

Opus 5 converts additional effort into better results more reliably than any earlier Opus model, so the setting carries more weight.

**Recommended approach:** start at `high` (the default), then adjust against your own evals.

- Step **down** to `low`/`medium` liberally wherever quality holds — these are now the primary control for token cost and latency, and produce strong quality at a fraction of the tokens.
- Step **up** to `xhigh` for demanding coding and agentic work, or `max` when a task justifies unconstrained token spend.
- If you carried an effort default over from a previous model, run a fresh sweep. Token allocation behind each level was recalibrated.
- At `xhigh` or `max`, set a large `max_tokens` — start at 64k and tune.
- Effort controls **thinking volume, not visible response length**. Lowering it will not reliably shorten answers.
- Effort is a request-level setting and changing it invalidates cached prefixes, so hold it constant within a cached conversation.
- Effort also affects tool-call behaviour: lower effort means fewer tool calls, more combined operations, less preamble.

---

## 4. Response length and verbosity

Opus 5's default user-facing responses run longer than prior Opus models'. Prompt for length explicitly.

Sample conciseness instruction (user-facing, multi-turn products):

```
Keep responses focused, brief, and concise. Keep disclaimers and caveats short, and spend most of the response on the main answer. When asked to explain something, give a high-level summary unless an in-depth explanation is specifically requested.
```

If your system prompt is long, pair it with a short reminder near the end:

```
<tone_preference>
Keep outputs reasonably concise.
</tone_preference>
```

---

## 5. Written deliverable length

Separate from conversational verbosity — files written to disk (reports, Markdown docs, summaries) also run longer.

```
Match the length of written documents to what the task needs: cover the substance, but do not pad with filler sections, redundant summaries, or boilerplate.
```

---

## 6. User-facing progress updates (agentic narration)

Opus 5 narrates readily during agentic work and announces what it's about to do. Per-message output in agentic sessions is longer than prior models'.

To tune narration **down**, describe the cadence and shape you want:

```
Before your first tool call, say in one sentence what you're about to do. While working, give a brief update only when you find something important or change direction. When you finish, lead with the outcome: your first sentence should answer "what happened" or "what did you find," with supporting detail after it for readers who want it.
```

To tune it **up** or change style, use the same lever in the other direction. Positive examples of the communication style you want beat instructions about what not to do.

---

## 7. Task scope and over-verification

**Remove verification instructions.** Opus 5 verifies its own work unprompted. Instructions like "include a final verification step for any non-trivial task" or "use a subagent to verify" cause over-verification; removing them reduces wasted tokens with no quality loss. The same applies to legacy harness scaffolding that adds separate verification steps.

**Constrain scope for narrow tasks**, since the model can add unrequested steps or reinterpret what the task should be:

```
Deliver what was asked, at the scope intended. Make routine judgment calls yourself, and check in only when different readings of the request would lead to materially different work. If the request seems mistaken or a better approach exists, say so in a sentence and continue with the task as asked rather than quietly narrowing, widening, or transforming it. Finish the whole task, and stop short of actions that are clearly beyond what was asked.
```

---

## 8. Controlling subagent spawning

Opus 5 delegates more readily than prior models. Delegation pays off on genuinely independent, sizeable tracks of work but multiplies cost and time on small tasks. Give explicit guidance, or set deterministic caps in your harness.

```
Delegate to a subagent only for large tasks that are genuinely independent and parallelizable, such as a wide multi-file investigation. Do not delegate work you can finish yourself in a handful of tool calls, and do not use subagents to verify or double-check your own work. If one subagent can complete the task, use one rather than several, and keep spawn counts low.
```

Note the counterweight: Opus 5 coordinates subagent teams well, with effective writer-verifier patterns and few cases of agents overwriting each other's work. Cap delegation for cost reasons, not quality ones.

---

## 9. Self-correction

Opus 5 catches and fixes its own mistakes well. Avoid instructing re-checks it already performs ("double-check your answer", "re-verify before responding") — they compound with existing behaviour and add cost without improving results.

It also narrates corrections to its earlier statements more than prior models, which can be undesirable in user-facing products:

```
Only correct an earlier statement when the error would change the user's code, conclusions, or decisions. State corrections plainly and briefly, then continue the task. For slips that change nothing for the user, make the fix and move on without noting it.
```

---

## 10. Running with thinking disabled

Thinking can be disabled only at effort `high` or below. With it disabled, two artifacts occasionally appear:

- **Tool calls as text.** The model writes a tool call into user-facing text instead of emitting a structured `tool_use` block. The turn completes normally, the call never runs, and in agentic loops the leaked text stays in conversation history and affects later turns. Most common on tool-heavy workloads like search.
- **Internal XML tags in output.** `<thinking>` or other internal tags can appear in the visible response. If your system prompt contains a rule telling the model not to think or not to reason, remove it — that increases tag leakage.

**Primary mitigation for both: keep thinking enabled and control cost with lower effort.** For most tasks, thinking on at `low` effort beats thinking off at similar cost.

For integrations that must keep thinking disabled, one combined instruction mitigates both:

```
When you use a tool, you may say a brief sentence first. If no tool can express what the user asked for, say so instead of guessing. Do not include internal or system XML tags in your response.
```

Instructions that name thinking tags specifically are *less* effective than this general form.

---

## 11. Capability notes that change how you prompt

- **Agentic coding.** Strongest on multi-file features, larger refactors, and end-to-end work. Completes full tasks rather than leaving stubs or placeholders. Performs best when given the complete task specification up front and left to run.
- **Code review and bug-finding.** High precision and recall; extra findings are mostly real issues, not false positives. Accuracy holds at lower effort, so a fast pass while writing plus a deeper pass before the PR both work. **Caveat:** if your review prompt says "only report high-severity issues" or "be conservative," the model takes it literally and reports less. Ask it to report everything and filter in a separate pass.
- **Vision.** Strong on charts, documents, diagrams, and UI/frontend replication. Re-validate any prompt-side vision workarounds tuned for earlier models — they may no longer be needed. Performance is strongest when the model has tools to iteratively analyse, crop, and visually verify; tool use is a more cost-effective lever than thinking alone.
- **Long context.** Instruction following, tool calling, and reasoning stay consistent across the full 1M window.
- **Office/document tasks.** Handles complex multi-sheet spreadsheets with non-trivial formulas and well-structured slide decks. Prompt it with the specific styles or templates it must follow.

---

## 12. Context engineering for Claude Code and custom harnesses

From Anthropic's 24 July 2026 blog post: they removed **over 80% of Claude Code's system prompt** for Opus 5 and Fable 5 with no measurable loss on coding evals. The core finding was over-constraint — conflicting instructions across system prompt, skills, and CLAUDE.md (e.g. "leave documentation as appropriate" alongside "DO NOT add comments") force the model to reason about which instruction wins before it can work on your problem.

Run `/doctor` in Claude Code (`claude doctor`) to rightsize skills and CLAUDE.md files automatically.

### Six shifts

| Then | Now |
|---|---|
| Give Claude rules | Let Claude use judgement |
| Give Claude examples | Design interfaces |
| Put it all upfront | Use progressive disclosure |
| Repeat yourself | Simple tool descriptions |
| Memory in CLAUDE.md | Auto-memory |
| Simple specs | Rich references |

**Rules → judgement.** The old system prompt said things like: default to no comments, never write multi-paragraph docstrings, don't create planning documents unless asked. The replacement is a single judgement-based line: *Write code that reads like the surrounding code: match its comment density, naming, and idiom.*

**Examples → interface design.** Examples now constrain the model to a narrow exploration space. Invest in tool/script/file design instead: expressive parameters, meaningful enums (e.g. a todo status of pending/in_progress/completed), and behaviour hinted through the interface rather than spelled out in prose.

**Upfront → progressive disclosure.** Move situational guidance (code review, verification) into skills the model calls when needed. Deferred-loading tools — where the agent must search for the full definition before use — let you expose more tools without paying context for all of them. Apply the same to CLAUDE.md and SKILL.md: a tree of files loaded at the right time beats one exhaustive document.

**Repetition → tool descriptions.** Instructions about how to use a tool belong in the tool description, not duplicated in the system prompt.

**Manual memory → auto-memory.** Claude now saves relevant memories itself rather than relying on `#` writes to CLAUDE.md.

**Simple specs → rich references.** Claude handles more complex references than markdown plans: HTML artifacts, a detailed test suite as a spec, a function in another codebase to port, rubrics used by verifier agents.

### Layer-by-layer

- **System prompt** — tied to product context: what product Claude is operating in and what it's doing. If you're building your own harness, spend your time here.
- **CLAUDE.md** — keep it lightweight. Briefly state what the repo is for, then spend most of the tokens on codebase gotchas (e.g. "all types live in one monolithic file"). Avoid stating what Claude can infer from the file tree. Use progressive disclosure: if you have verification instructions, make them a skill and reference it.
- **Skills** — lightweight guides for finding information when needed. Avoid over-constraining except in high-stakes areas. Split long skills across multiple files. Best used to encode opinions, knowledge, or practices specific to you, your team, or your product.
- **References** — `@` mention files. Prefer code over description: an HTML mockup beats a written description or a screenshot, because code is high-fidelity instruction in a language the model knows well.

---

## 13. Cross-model best practices still worth applying

From the general prompting best practices page (applies to all current models, with Opus 5 exceptions noted):

- **Be clear and direct.** Golden rule: if a colleague with minimal context would be confused by your prompt, so will Claude. Explicitly request "above and beyond" behaviour rather than hoping it's inferred.
- **Add motivation.** Explaining *why* an instruction matters helps the model generalise correctly.
- **Examples.** 3–5, relevant and diverse, wrapped in `<example>` / `<examples>` tags. (Note the tension with the context-engineering post above: for *general context* like tool usage, examples now over-constrain — this advice applies best to output format, tone, and structure in task-specific prompts.)
- **XML structure.** Consistent, descriptive tags; nest where there's natural hierarchy.
- **Long-context.** Put long documents *above* your query and instructions — queries at the end improved response quality by up to 30% in testing. Wrap documents in `<document>` with `<document_content>` and `<source>` subtags. Ask the model to quote relevant parts first before acting.
- **Formatting.** Tell it what to do, not what not to do. Match your prompt's style to the desired output style (removing markdown from your prompt reduces markdown in the output). LaTeX is the default for maths — ask for plain text explicitly if you don't want it.
- **Tool triggering.** Be explicit about wanting action rather than suggestions. Dial back aggressive tool language ("CRITICAL: You MUST use this tool when...") to normal phrasing — newer models overtrigger on it.
- **Parallel tool calls.** Already high success rate; a `<use_parallel_tool_calls>` block pushes it to ~100%.
- **Prefill is gone.** Prefilled assistant messages return a 400 error on 4.6-and-later models. Use system prompt instructions, structured outputs, or `output_config.format`.
- **Self-check instructions** ("verify your answer against [test criteria]") are the documented exception for Opus 5 — remove them rather than rewriting them.
- **Manual chain-of-thought** as a fallback with thinking off: on Opus 5, prefer keeping thinking on at lower effort instead, because of the XML tag leakage described in §10.

Model self-knowledge snippets, if your app needs Claude to identify itself:

```
The assistant is Claude, created by Anthropic. The current model is Claude Opus 5.
```

```
When an LLM is needed, please default to Claude Opus 5 unless the user requests
otherwise. The exact model string for Claude Opus 5 is claude-opus-5.
```

---

## 14. Migration checklist: Opus 4.8 → Opus 5

Drop-in at the same pricing, with two breaking changes.

**Breaking**

1. **Thinking on by default.** Requests without a `thinking` field now run with adaptive thinking. Revisit `max_tokens` (hard limit on thinking + response text). To preserve old behaviour, pass `thinking: {"type": "disabled"}` — subject to item 2.
2. **Disabling thinking is capped at `high` effort.** `thinking: {"type": "disabled"}` with effort `xhigh` or `max` returns a 400 error, validated per request. Either re-enable thinking or lower effort.

**Recommended**

- Re-run an effort sweep on your own evals rather than carrying settings over. Test `low`/`medium` as cost controls and `max` for capability-critical work. At `xhigh`/`max`, raise `max_tokens` to ≥64k.
- Re-tune length and verbosity prompts (§4, §5).
- Remove carried-over verification and self-check instructions (§7).
- Constrain scope explicitly for narrow tasks; steer or cap subagent delegation (§8).
- Review prompts near the caching minimum — 512 tokens now caches.
- Consider `fallbacks: "default"` (beta): Opus 5 ships cybersecurity safety classifiers whose cyber-category refusals can fall back to Opus 4.8. Handle `stop_reason: "refusal"`.
- Consider mid-conversation tool changes (beta) and task budgets (beta) for agentic workloads.
- If you use **web fetch**, plan an alternative — not available on Opus 5.
- If you have a **Priority Tier** commitment, plan capacity separately — not supported on Opus 5.
- Re-baseline cost and latency.

Claude Code shortcut: `/claude-api migrate this project to claude-opus-5` invokes the bundled Claude API skill, which applies the ID swap, breaking parameter changes, prefill replacement, and effort calibration, then produces a manual-verification checklist.

---

## 15. Adjacent: working patterns for Claude 5 generation models

From the Fable field guide (written for Fable 5 but referenced by Anthropic as the guidance for prompting the newest generation, and directly applicable to Opus 5's long-horizon agentic work). The framing: quality is now bottlenecked by your ability to clarify your *unknowns*, not by the model.

**Pre-implementation**

- **Blind spot pass** — literally ask for one. *"I'm working on adding a new auth provider but I know nothing about the auth modules in this codebase. Can you do a blind spot pass to help me figure out my relevant unknown unknowns and help me prompt you better."*
- **Brainstorm and prototype** — cheap way to surface the criteria you only recognise when you see them. *"Make me an HTML page with 4 wildly different design directions so I can react to them."*
- **Interviews** — *"Interview me one question at a time about anything ambiguous, prioritize questions where my answer would change the architecture."*
- **References** — source code is the best reference. Point it at the folder, even in another language.
- **Implementation plans** — ask for the plan to lead with the decisions you're most likely to change (data models, type interfaces, UX flows) and bury the mechanical refactoring.

**During implementation**

- **Implementation notes** — *"Keep an implementation-notes.md file. If you hit an edge case that forces you to deviate from the plan, pick the conservative option, log it under 'Deviations', and keep going."*

**Post-implementation**

- **Pitches and explainers** — package prototype, spec, and notes into one doc for buy-in.
- **Quizzes** — ask for a report on the change with a quiz at the bottom; merge only after you pass it.

Also relevant: too specific and Claude follows instructions even when a pivot would be better; too vague and it falls back on generic industry best practices. Give it context on where you are in your thinking and what you already know.
