# Cloud and AI Tagging Policy (Nimbus Holdings)

*Written for this project as a realistic example. Not a real company's policy.*

## Purpose
Every cost must have an owner. Tags are how a charge is allocated to the team that caused it, so that
showback reports, budgets and investigations are accurate.

## Required tags
Every billable resource and every AI API key must carry exactly these three tag keys, spelled exactly
as shown, in lower case, with no spaces:

| Key | Meaning | Example |
|---|---|---|
| `team` | The owning team | `payments` |
| `environment` | `prod`, `staging` or `dev` | `prod` |
| `application` | The application or service name | `PayCore` |

Aliases such as `env`, `Team` or ` team` (with a leading space) are **not compliant**, even though the
finance team can usually still work out the owner.

## Untagged spend
- Untagged spend is reported weekly as "unallocated".
- Unallocated spend above **2% of monthly spend** must be investigated within 5 working days.
- Any single untagged resource costing more than **$1,000 in a month** is a policy breach. The account
  owner of the account it runs in is responsible for it.
- GPU instances must always be tagged before launch. Untagged GPU instances may be stopped without notice.

## Shared costs
Organisation-wide contracts (support plans, enterprise agreements) are tagged `team = shared` and split
across teams in proportion to their spend at quarter end.

## Exceptions
Exceptions need written approval from the FinOps lead and expire after 90 days.
