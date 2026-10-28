# AI Model Usage Policy (Nimbus Holdings)

*Written for this project as a realistic example. Not a real company's policy.*

## Default models
Use the smallest model that meets the quality bar. Defaults by use case:

| Use case | Default model tier | Example models |
|---|---|---|
| Customer-support chatbots, classification, routing | Small (fast, low cost) | claude-haiku-4-5, gpt-5-mini |
| Search answers and retrieval | Small | gpt-5-mini, claude-haiku-4-5 |
| Marketing content drafts | Small | claude-haiku-4-5 |
| Research and evaluation | Any tier, within the research budget | claude-sonnet-4-5 |

## Moving to a larger model
Switching a **production** workload to a larger, more expensive model tier requires:
1. An evaluation showing the quality gain, and
2. **FinOps approval** with a cost estimate, when the change adds more than $2,000 per month.

Approval by the team lead alone is not enough for production model upgrades.

## Token hygiene
- Every API key must be tagged with `team`, `environment` and `application` (see the tagging policy).
- Batch jobs must cap retries (maximum 2) and use exponential back-off. Unbounded retries are a known
  cause of sudden token spikes.
- Cache repeated prompts where the provider supports prompt caching.

## Monitoring
Daily AI spend per team is monitored. A day more than 2.5 times the team's typical day triggers an alert.
