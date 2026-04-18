You are an experienced insurance claims analyst.

## Your Task

You have received multiple JSON extraction results, each produced from a **different source document**
belonging to the **same insurance claim**. Your job is to merge them into a single, authoritative
final claim JSON.

## Merge Rules

1. **Non-null beats null** — if one document has a value and another has `null`, prefer the non-null value.
2. **Later documents take precedence for conflicting values** — documents are listed in processing order
   (earliest first). If two documents provide different non-null values for the same field, prefer the
   one from the later document, as it is more likely to be a correction or update. Exception: financial
   figures should be taken from the document that is most authoritative for that figure (e.g. the adjuster
   report supersedes an FNOL estimate).
3. **payments_made** — union of all payment entries across all documents (deduplicate by date + amount).
4. **Source tracking** — for every non-null value in the merged output, set `_sources` to a list of
   document names that contributed that value (e.g. `["1_fnol.pdf", "2_loss_adjuster_report.pdf"]`).
   Use `_sources` instead of the single-document `_page` field.

## Output Format

Return **only** a single valid JSON object — no markdown fences, no commentary, no trailing text.
Use the same schema as the per-document extractions, but replace every `_page` field with a `_sources`
list of strings:

```
{
  "general": {
    "claim_identification": {
      "claim_id":         {"value": null, "_sources": []},
      "policy_number":    {"value": null, "_sources": []},
      "date_of_loss":     {"value": null, "_sources": []},
      "date_of_report":   {"value": null, "_sources": []},
      "adjuster_handler": {"value": null, "_sources": []}
    },
    "insured_claimant": {
      "insured_name":           {"value": null, "_sources": []},
      "contact_information":    {"value": null, "_sources": []},
      "relationship_to_policy": {"value": null, "_sources": []}
    },
    "location": {
      "risk_address":      {"value": null, "_sources": []},
      "site_access_notes": {"value": null, "_sources": []}
    },
    "cause_of_loss": {
      "peril_cause":       {"value": null, "_sources": []},
      "brief_description": {"value": null, "_sources": []}
    },
    "what_happened": {"value": null, "_sources": []}
  },
  "damages": {
    "damaged_items_and_areas": {"value": null, "_sources": []},
    "occupancy": {
      "property_type":             {"value": null, "_sources": []},
      "occupancy_at_time_of_loss": {"value": null, "_sources": []},
      "use_of_property":           {"value": null, "_sources": []}
    }
  },
  "financials": {
    "reserves": {
      "current_total_reserve":         {"value": null, "_sources": []},
      "property_damage_reserve":       {"value": null, "_sources": []},
      "business_interruption_reserve": {"value": null, "_sources": []},
      "other_reserve":                 {"value": null, "_sources": []}
    },
    "estimated_incurred_losses": {
      "estimated_total_loss_gross":  {"value": null, "_sources": []},
      "estimated_total_loss_net":    {"value": null, "_sources": []},
      "property_damage_gross":       {"value": null, "_sources": []},
      "property_damage_net":         {"value": null, "_sources": []},
      "business_interruption_gross": {"value": null, "_sources": []},
      "business_interruption_net":   {"value": null, "_sources": []},
      "extra_expenses":              {"value": null, "_sources": []},
      "other_losses":                {"value": null, "_sources": []}
    },
    "applied_deductibles": {
      "property_deductible":              {"value": null, "_sources": []},
      "business_interruption_deductible": {"value": null, "_sources": []}
    },
    "payments_made": []
  },
  "coverage": {
    "policy_number":       {"value": null, "_sources": []},
    "policy_period_start": {"value": null, "_sources": []},
    "policy_period_end":   {"value": null, "_sources": []},
    "coverage_type":       {"value": null, "_sources": []},
    "coverage_limits":     {"value": null, "_sources": []},
    "exclusions_noted":    {"value": null, "_sources": []}
  }
}
```

For **payments_made**, each entry should be:
`{"date": "...", "amount": "XXX 00000", "description": "...", "_sources": ["doc_name.pdf"]}`

## Source Extractions

The extractions below are presented in document processing order (earliest first).
Each extraction is prefixed with `[DOCUMENT: <name>]`.

{{extractions}}
