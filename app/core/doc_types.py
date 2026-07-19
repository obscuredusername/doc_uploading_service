"""
Canonical registry of document types the collection portal can request.

This is structured reference data (slug -> human label), not env config, so
it lives here rather than in .env. The API mints one upload link per slug in
this registry (Phase 2), and the public upload page renders the matching
label (Phase 3).

Order is preserved (insertion order) so the generated link list is stable.
"""
from collections import OrderedDict

# slug -> display label. Slugs are the URL segment: domain.com/<ref>/<slug>.
DOCUMENT_TYPES: "OrderedDict[str, str]" = OrderedDict(
    [
        ("id_proof", "ID Proof (Legacy)"),
        ("proof_of_name", "Proof of Name"),
        ("proof_of_address", "Proof of Address"),
        ("income_proof", "Income Proof"),
        ("bank_statement", "Bank Statement"),
        ("creditor_statement", "Creditor Statement"),
        ("creditor_document", "Creditor Document"),
        ("creditor_report", "Creditor Report"),
        ("credit_journal", "Credit Journal"),
        ("vehicle_finance", "Vehicle Finance"),
        ("vehicle_valuation", "Vehicle Valuation"),
        ("mortgage_statement", "Mortgage Statement"),
        ("tenancy_agreement", "Tenancy Agreement"),
        ("boarding_letter", "Boarding Letter"),
        ("lodging_letter", "Lodging Letter"),
        ("proof_of_rent", "Proof of Rent"),
        ("council_tax_bill", "Council Tax Bill"),
        ("immigration_status", "Immigration Status"),
        ("hmrc_webchat", "HMRC Webchat"),
        ("tax_return", "Tax Return"),
        ("cis_slip", "CIS Sub-Contractor Slip"),
        ("student_loan_statement", "Student Loan Statement"),
        ("pension_statement", "Pension Statement"),
        ("employment_contract", "Contract of Employment"),
        ("gamstop", "GamStop Check"),
        ("criteria_check", "Criteria Check Result"),
        ("other", "Other"),
    ]
)


def all_slugs() -> list[str]:
    """Every doc-type slug, in canonical order."""
    return list(DOCUMENT_TYPES.keys())


def is_valid_slug(slug: str) -> bool:
    return slug in DOCUMENT_TYPES


def label_for(slug: str) -> str:
    """Human label for a slug; falls back to the slug itself if unknown."""
    return DOCUMENT_TYPES.get(slug, slug)
