# Commitments, Discounts and Credits Register (Nimbus Holdings)

*Written for this project as a realistic example. Not a real company's contracts.*

## Active and past commitments

### SP-PAY-2025: AWS Compute Savings Plan (payments)
- Covers: payments team EC2 usage (m5 family) in the Orion Zenith account
- Term: 1 May 2025 to **30 April 2026** (12 months)
- Discount: about 28% off on-demand rates for covered usage
- Renewal: requires FinOps approval 30 days before expiry. **No renewal decision is recorded.**
- When a Savings Plan expires, the same usage is charged at on-demand rates, so cost rises with no
  change in usage.

## Credits

### Google Cloud for Startups credit (data-platform)
- Applies to: Google Cloud usage in the data-platform team (BigQuery)
- Amount: about $2,400 per month, applied as a credit line on the bill
- End date: **31 August 2026**. From September 2026 the same usage is billed in full.

## One-time purchases

### AWS Business Support (shared)
- Annual prepayment, renewed each June for 12 months
- Charged once, as a one-time purchase, and tagged `team = shared`
- This is expected yearly spend, not an anomaly.

## How to read these on a bill (FOCUS columns)
- Commitment-covered usage has `PricingCategory = Committed` and a `CommitmentDiscountId`.
- Credits are rows with `ChargeCategory = Credit` and negative cost.
- Prepayments are rows with `ChargeCategory = Purchase` and `ChargeFrequency = One-Time`.
