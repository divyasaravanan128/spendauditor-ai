# FOCUS Glossary: the bill columns this app uses

*A plain-language summary written for this project, based on the public FOCUS specification by the FinOps
Foundation (focus.finops.org). Read the specification for authoritative definitions.*

## What FOCUS is
FOCUS (FinOps Open Cost and Usage Specification) is an open standard for billing data. AWS, Google
Cloud, Microsoft Azure and Oracle can all export bills in this shape, so one loader handles them all.

## Cost columns
- **ListCost**: what the usage would cost at public list prices, before any discount.
- **EffectiveCost**: the true cost after discounts and with commitment purchases spread over the usage
  they cover. Use this to compare spend over time. This app's default metric.
- **BilledCost**: what appears on the invoice for this period. Credits reduce it.

## Describing a charge
- **ChargeCategory**: `Usage`, `Purchase`, `Credit`, `Adjustment` or `Tax`.
- **ChargeFrequency**: `Usage-Based`, `Recurring` or `One-Time`.
- **PricingCategory**: `Standard` (on-demand), `Committed` (covered by a commitment), `Dynamic` (spot).
- **ServiceName / ServiceCategory**: the provider's service and a common category such as Compute,
  Storage or AI and Machine Learning.
- **SubAccountName**: the account, subscription or project that ran the resource.
- **ConsumedQuantity / ConsumedUnit**: how much was used, in what unit (hours, GB, tokens).
- **Tags**: key-value labels used to allocate cost to an owner.

## AI token columns (custom, ahead of the standard)
FOCUS versions up to 1.4 have no columns for AI model names or token types. The standard allows custom
columns prefixed `x_`, so this app adds:
- **x_Model**: the AI model used, for example `claude-haiku-4-5`.
- **x_TokenType**: `input` or `output` tokens.
The FinOps Foundation has announced plans to add token columns in a later version.

## Usage, price and mix
A change in cost can come from three places:
- **Usage (volume)**: more units consumed at the same prices.
- **Price (rate)**: the same units cost more or less per unit, for example when a discount ends.
- **Mix**: usage shifts towards more expensive items, for example moving traffic to a larger model.
Credits ending and one-time purchases are separate effects and should not be reported as usage growth.
