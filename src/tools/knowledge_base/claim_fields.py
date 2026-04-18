"""
claim_fields.py
===============
Data model for all required claim fields.

Each field is a subclass of :class:`Field` and carries:

* ``field_name``    — dot-notation path that mirrors the JSON extraction schema
                      (e.g. ``"general.claim_identification.claim_id"``).
* ``description``   — what the field represents and where to look for it.
* ``example_value`` — a realistic example extracted from a real document.
* ``field_type``    — a :class:`~field_types.FieldType` instance that drives
                      normalization and value comparison.

Registries
----------
CLAIM_FIELDS : list[Field]
    All field instances in the canonical order that mirrors diary_template.md.

FIELD_REGISTRY : dict[str, Field]
    Same fields keyed by ``field_name`` for O(1) lookup.
"""

from __future__ import annotations

from abc import ABC

from src.tools.knowledge_base.field_types import (
    CurrencyType,
    DateType,
    EnumType,
    FieldType,
    NarrativeType,
    StringType,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared type singletons
# ─────────────────────────────────────────────────────────────────────────────

_str = StringType()
_narrative = NarrativeType()
_date = DateType()
_currency = CurrencyType()
_property_type_enum = EnumType(
    allowed_values=["residential", "commercial", "industrial", "mixed"],
    aliases={
        "res": "residential",
        "residential property": "residential",
        "comm": "commercial",
        "commercial property": "commercial",
        "ind": "industrial",
    },
)


# ─────────────────────────────────────────────────────────────────────────────
# Base class
# ─────────────────────────────────────────────────────────────────────────────


class Field(ABC):
    """
    Abstract base for a single extractable claim field.

    All attributes are **class variables** — each subclass defines them
    statically so instances are lightweight singletons.
    """

    #: Dot-notation JSON path (mirrors the extraction schema).
    field_name: str

    #: Plain-English description of what to extract and where to find it.
    description: str

    #: A realistic example of a valid extracted value.
    example_value: str

    #: The :class:`FieldType` that governs normalization and comparison.
    field_type: FieldType

    def normalize(self, value: str | None) -> str | None:
        """Delegate to the field's type normalizer."""
        return self.field_type.normalize(value)

    def compare(self, a: str | None, b: str | None) -> bool:
        """Delegate to the field's type comparator."""
        return self.field_type.compare(a, b)

    def __repr__(self) -> str:
        return f"<Field:{self.field_name}>"


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — General › Claim Identification
# ─────────────────────────────────────────────────────────────────────────────


class ClaimIdField(Field):
    field_name = "general.claim_identification.claim_id"
    description = (
        "Unique alphanumeric identifier assigned to this claim by the insurer "
        "or claims management system."
    )
    example_value = "CLM-2026-000987"
    field_type = _str


class PolicyNumberField(Field):
    field_name = "general.claim_identification.policy_number"
    description = (
        "Policy contract number under which the claim is filed. "
        "May appear as 'Policy No.', 'Policy #', or 'Contract No.'."
    )
    example_value = "POL-123456789"
    field_type = _str


class DateOfLossField(Field):
    field_name = "general.claim_identification.date_of_loss"
    description = (
        "Calendar date on which the insured event (loss, damage, incident) occurred. "
        "Look for 'Date of Loss', 'Incident Date', or 'Event Date'."
    )
    example_value = "2026-03-28"
    field_type = _date


class DateOfReportField(Field):
    field_name = "general.claim_identification.date_of_report"
    description = (
        "Date on which the claim or report document was issued or submitted. "
        "Look for 'Report Date', 'Date of Report', or the document header date."
    )
    example_value = "2026-04-02"
    field_type = _date


class AdjusterHandlerField(Field):
    field_name = "general.claim_identification.adjuster_handler"
    description = (
        "Name or role of the person or firm handling the claim on behalf of the insurer. "
        "May appear as 'Adjuster', 'Handler', 'Loss Adjuster', or 'Assigned To'."
    )
    example_value = "Jane Smith / Global Loss Adjusters Ltd."
    field_type = _str


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — General › Insured / Claimant
# ─────────────────────────────────────────────────────────────────────────────


class InsuredNameField(Field):
    field_name = "general.insured_claimant.insured_name"
    description = (
        "Full legal name of the insured party or claimant. "
        "May appear as 'Insured', 'Policyholder', 'Claimant Name'."
    )
    example_value = "Max Mustermann"
    field_type = _str


class ContactInformationField(Field):
    field_name = "general.insured_claimant.contact_information"
    description = (
        "Phone number, email address, or postal address for the insured/claimant. "
        "Extract all contact details found; separate multiple entries with ' | '."
    )
    example_value = "+49 123 456789 | max.mustermann@example.com"
    field_type = _str


class RelationshipToPolicyField(Field):
    field_name = "general.insured_claimant.relationship_to_policy"
    description = (
        "The claimant's relationship to the insurance policy "
        "(e.g. 'policyholder', 'named insured', 'third party', 'beneficiary')."
    )
    example_value = "policyholder"
    field_type = _str


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — General › Location
# ─────────────────────────────────────────────────────────────────────────────


class RiskAddressField(Field):
    field_name = "general.location.risk_address"
    description = (
        "Full postal address of the insured risk / damaged property. "
        "Look for 'Risk Address', 'Property Address', 'Site Address', 'Location of Loss'."
    )
    example_value = "Musterstraße 1, 80331 Munich, Germany"
    field_type = _str


class SiteAccessNotesField(Field):
    field_name = "general.location.site_access_notes"
    description = (
        "Any notes about accessing the damaged site (e.g. key holder, locked gate, "
        "restricted access hours). Extract verbatim if present."
    )
    example_value = "Key available from property manager. Access Mon–Fri 08:00–17:00."
    field_type = _narrative


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — General › Cause of Loss
# ─────────────────────────────────────────────────────────────────────────────


class PerilCauseField(Field):
    field_name = "general.cause_of_loss.peril_cause"
    description = (
        "The named peril or primary cause of the loss event "
        "(e.g. 'Fire', 'Water Damage – Burst Pipe', 'Storm', 'Theft', 'Flood')."
    )
    example_value = "Water Damage (Burst Pipe)"
    field_type = _str


class BriefDescriptionField(Field):
    field_name = "general.cause_of_loss.brief_description"
    description = (
        "A short (1–3 sentence) factual description of the loss event as stated in the document. "
        "Do not paraphrase; extract the closest matching passage."
    )
    example_value = (
        "A burst pipe beneath the kitchen sink caused significant water discharge "
        "spreading across the kitchen and adjacent living room."
    )
    field_type = _narrative


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — General › What Happened
# ─────────────────────────────────────────────────────────────────────────────


class WhatHappenedField(Field):
    field_name = "general.what_happened"
    description = (
        "Full narrative of the incident as reconstructed from the document: "
        "sequence of events, immediate actions taken, parties involved. "
        "Extract the most detailed passage available."
    )
    example_value = (
        "The policyholder discovered a burst pipe beneath the kitchen sink on the "
        "morning of 28 March 2026. Water spread across the kitchen floor and into the "
        "adjacent living room. The main water supply was shut off and an emergency "
        "plumber was called within two hours."
    )
    field_type = _narrative


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Damages › Damaged Items and Areas
# ─────────────────────────────────────────────────────────────────────────────


class DamagedItemsField(Field):
    field_name = "damages.damaged_items_and_areas"
    description = (
        "Description of all damaged structures, areas, contents, equipment, or stock. "
        "Include item names and estimated costs where stated."
    )
    example_value = (
        "Hardwood flooring (EUR 4850); Kitchen cabinets (EUR 3450); "
        "Electrical outlets/wiring (EUR 1300); Living room furniture (EUR 2250); "
        "Drying/dehumidification services (EUR 2050)."
    )
    field_type = _narrative


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Damages › Occupancy
# ─────────────────────────────────────────────────────────────────────────────


class PropertyTypeField(Field):
    field_name = "damages.occupancy.property_type"
    description = (
        "Classification of the damaged property. "
        "One of: residential, commercial, industrial, mixed."
    )
    example_value = "residential"
    field_type = _property_type_enum


class OccupancyAtTimeOfLossField(Field):
    field_name = "damages.occupancy.occupancy_at_time_of_loss"
    description = (
        "Whether and how the property was occupied at the time of the loss event "
        "(e.g. 'owner-occupied', 'tenant-occupied', 'vacant', 'under renovation')."
    )
    example_value = "owner-occupied"
    field_type = _str


class UseOfPropertyField(Field):
    field_name = "damages.occupancy.use_of_property"
    description = (
        "Stated purpose or use of the property at the time of loss "
        "(e.g. 'primary residence', 'retail shop', 'warehouse', 'office')."
    )
    example_value = "primary residence"
    field_type = _str


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Financials › Reserves
# ─────────────────────────────────────────────────────────────────────────────


class CurrentTotalReserveField(Field):
    field_name = "financials.reserves.current_total_reserve"
    description = "Total reserve set by the insurer for this claim across all categories."
    example_value = "EUR 15000"
    field_type = _currency


class PropertyDamageReserveField(Field):
    field_name = "financials.reserves.property_damage_reserve"
    description = "Reserve allocated specifically for property damage losses."
    example_value = "EUR 13900"
    field_type = _currency


class BusinessInterruptionReserveField(Field):
    field_name = "financials.reserves.business_interruption_reserve"
    description = "Reserve allocated for business interruption / loss of income losses."
    example_value = "EUR 5000"
    field_type = _currency


class OtherReserveField(Field):
    field_name = "financials.reserves.other_reserve"
    description = "Reserve allocated for any other category not covered above."
    example_value = "EUR 1000"
    field_type = _currency


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Financials › Estimated / Incurred Losses
# ─────────────────────────────────────────────────────────────────────────────


class EstimatedTotalLossGrossField(Field):
    field_name = "financials.estimated_incurred_losses.estimated_total_loss_gross"
    description = (
        "Total estimated or incurred loss before deductibles are applied (gross amount). "
        "Sum of all damage categories."
    )
    example_value = "EUR 13900"
    field_type = _currency


class EstimatedTotalLossNetField(Field):
    field_name = "financials.estimated_incurred_losses.estimated_total_loss_net"
    description = (
        "Total estimated or incurred loss after deductibles are applied (net amount). "
        "Only extract if explicitly stated or clearly derivable."
    )
    example_value = "EUR 12400"
    field_type = _currency


class PropertyDamageGrossField(Field):
    field_name = "financials.estimated_incurred_losses.property_damage_gross"
    description = "Estimated property damage loss before deductibles (gross)."
    example_value = "EUR 13900"
    field_type = _currency


class PropertyDamageNetField(Field):
    field_name = "financials.estimated_incurred_losses.property_damage_net"
    description = "Estimated property damage loss after deductibles (net)."
    example_value = "EUR 12400"
    field_type = _currency


class BusinessInterruptionGrossField(Field):
    field_name = "financials.estimated_incurred_losses.business_interruption_gross"
    description = "Estimated business interruption loss before deductibles (gross)."
    example_value = "EUR 8000"
    field_type = _currency


class BusinessInterruptionNetField(Field):
    field_name = "financials.estimated_incurred_losses.business_interruption_net"
    description = "Estimated business interruption loss after deductibles (net)."
    example_value = "EUR 6500"
    field_type = _currency


class ExtraExpensesField(Field):
    field_name = "financials.estimated_incurred_losses.extra_expenses"
    description = (
        "Extra or additional expenses incurred as a direct consequence of the loss "
        "(e.g. temporary relocation, emergency repairs, equipment rental)."
    )
    example_value = "EUR 2050"
    field_type = _currency


class OtherLossesField(Field):
    field_name = "financials.estimated_incurred_losses.other_losses"
    description = "Any other financial losses not captured in the categories above."
    example_value = "EUR 500"
    field_type = _currency


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Financials › Applied Deductibles
# ─────────────────────────────────────────────────────────────────────────────


class PropertyDeductibleField(Field):
    field_name = "financials.applied_deductibles.property_deductible"
    description = (
        "Deductible applied to the property damage component of the claim. "
        "May appear as 'Excess', 'Deductible', 'Self-Retention'."
    )
    example_value = "EUR 1500"
    field_type = _currency


class BusinessInterruptionDeductibleField(Field):
    field_name = "financials.applied_deductibles.business_interruption_deductible"
    description = "Deductible applied to the business interruption component of the claim."
    example_value = "EUR 1500"
    field_type = _currency


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Coverage
# ─────────────────────────────────────────────────────────────────────────────


class CoveragePolicyNumberField(Field):
    field_name = "coverage.policy_number"
    description = (
        "Policy number as stated in the coverage section. "
        "Should match general.claim_identification.policy_number."
    )
    example_value = "POL-123456789"
    field_type = _str


class PolicyPeriodStartField(Field):
    field_name = "coverage.policy_period_start"
    description = "Start date of the policy period (inception date)."
    example_value = "2026-01-01"
    field_type = _date


class PolicyPeriodEndField(Field):
    field_name = "coverage.policy_period_end"
    description = "End date (expiry date) of the policy period."
    example_value = "2026-12-31"
    field_type = _date


class CoverageTypeField(Field):
    field_name = "coverage.coverage_type"
    description = (
        "Type or name of the coverage triggered by this claim "
        "(e.g. 'Property All-Risk', 'Water Damage', 'Business Interruption')."
    )
    example_value = "Property All-Risk – Water Damage"
    field_type = _str


class CoverageLimitsField(Field):
    field_name = "coverage.coverage_limits"
    description = (
        "Maximum insured amount or sum insured under the relevant coverage section. "
        "Extract as a currency amount if stated numerically."
    )
    example_value = "EUR 500000"
    field_type = _currency


class ExclusionsNotedField(Field):
    field_name = "coverage.exclusions_noted"
    description = (
        "Any policy exclusions mentioned in the document that are relevant to this claim. "
        "Extract verbatim or summarise if a list."
    )
    example_value = "No exclusions applicable at this stage, subject to final policy review."
    field_type = _narrative


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

#: Ordered list of all claim field instances (mirrors diary_template.md section order).
CLAIM_FIELDS: list[Field] = [
    # ── 1. General ─────────────────────────────────────────────────
    ClaimIdField(),
    PolicyNumberField(),
    DateOfLossField(),
    DateOfReportField(),
    AdjusterHandlerField(),
    InsuredNameField(),
    ContactInformationField(),
    RelationshipToPolicyField(),
    RiskAddressField(),
    SiteAccessNotesField(),
    PerilCauseField(),
    BriefDescriptionField(),
    WhatHappenedField(),
    # ── 2. Damages ─────────────────────────────────────────────────
    DamagedItemsField(),
    PropertyTypeField(),
    OccupancyAtTimeOfLossField(),
    UseOfPropertyField(),
    # ── 3. Financials ──────────────────────────────────────────────
    CurrentTotalReserveField(),
    PropertyDamageReserveField(),
    BusinessInterruptionReserveField(),
    OtherReserveField(),
    EstimatedTotalLossGrossField(),
    EstimatedTotalLossNetField(),
    PropertyDamageGrossField(),
    PropertyDamageNetField(),
    BusinessInterruptionGrossField(),
    BusinessInterruptionNetField(),
    ExtraExpensesField(),
    OtherLossesField(),
    PropertyDeductibleField(),
    BusinessInterruptionDeductibleField(),
    # ── 4. Coverage ────────────────────────────────────────────────
    CoveragePolicyNumberField(),
    PolicyPeriodStartField(),
    PolicyPeriodEndField(),
    CoverageTypeField(),
    CoverageLimitsField(),
    ExclusionsNotedField(),
]

#: O(1) lookup by field_name dot-path.
FIELD_REGISTRY: dict[str, Field] = {f.field_name: f for f in CLAIM_FIELDS}
