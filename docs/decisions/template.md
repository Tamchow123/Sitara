# NNNN — Title of the decision

- **Status:** proposed | accepted | superseded by NNNN
- **Date:** YYYY-MM-DD
- **Deciders:** who made or approved the decision
- **Phase:** which roadmap phase this belongs to (see ../PHASES.md)

## Context

What situation or question forced a decision. Include the constraints that mattered (cost, cultural accuracy, rights, MVP boundaries) and any experiment or evidence gathered — for model evaluations, record the exact provider model identifiers **and versions** tested, the prompt matrix used, the budget spent, and where the scoring artefacts live.

## Decision

The choice made, stated plainly. If the decision selects a model, tool, or provider, note that the selection is time-sensitive and where it is configured (environment variable name), not hard-coded.

## Consequences

What becomes easier, what becomes harder, what is deferred, and what would trigger revisiting this decision.

## Alternatives considered

Each alternative with the reason it was not chosen.

## Model-evaluation fields (optional — include for model/provider decisions)

- **Exact identifiers and versions tested:** `owner/model` plus version/digest
  where the provider exposes one (note when models are versionless/latest).
- **Pricing verification date:** when each price was checked, and the source URL.
- **Provider terms verification date:** when licence/retention/training terms
  were checked, and what remains unresolved.
- **API input schema:** the input parameters relied upon (seed, aspect ratio,
  reference/edit params, prompt format support) as verified on that date.
- **Experiment commit hash:** the git commit of the experiment code that
  produced the results.
- **Total experiment spend:** reconciled totals from the budget ledger(s),
  per run.
- **Output and scoring artefact locations:** run IDs, result/record paths,
  contact sheets, completed scoring sheets.
