# Data curation log — 2026-11-22

## Scope

Expanded the priority 12 civil procedure set (Law 13/1968) because the seed previously had only Article 63. Added citizen-facing summaries for Articles 65 (filing attachments and handling a rejected filing), 67 (registration and transfer for service), 68 (service and when the case is considered joined), and 70 (a request to deem a case not filed after a specified failure to serve attributable to the claimant).

Added a separate legislation-relationship record for Amendment Law 157/2024. It captures its Official Gazette issue/date, effective date, affected threshold articles, threshold substitutions, and transition treatment for cases filed earlier. The relationship record is intentionally distinct from article records so retrieval can answer both the general procedure and the current amendment state.

## Source and currentness controls

- Law 13/1968 article summaries link to the consolidated Manshurat source as a secondary legal repository. The record marks each entry as a concise paraphrase and points out material amendments and exceptions.
- Amendment Law 157/2024 is linked to the Ministry of Finance legislation index, which lists it as in force and identifies the Official Gazette issue. A legal text mirror was used to cross-check the amendment scope and date; the government page warns that the Gazette is the legal reference.
- Threshold values are categorized as jurisdiction and appeal thresholds, not filing fees. The transition note preserves the special treatment of cases filed before the amendment took effect.
- No claim is made that the seed contains all Egyptian laws or all amendments. Check the Official Gazette and case-specific law before user-facing legal guidance.

## Files updated

- `Datasets/egypt_citizen_law_seed.jsonl`: four article records and one amendment relationship record; one JSON object per line.
- `Datasets/citizen_law_priority_catalog.json`: updated priority-12 article coverage, amendment relationship, and next-gap note.
- `Datasets/legal_sources_catalog.json`: expanded secondary source scope and added the government amendment index with limitations.
- `README.md`: documented the new data scope and distinction between amendment metadata and article summaries.

## Data quality review

Parsed every JSONL row and both JSON catalogs after the update; checked unique record IDs, required provenance fields for article records, and that priority coverage matches the inserted articles. Temporary PDF previews and unrelated working files were not included in the source-control change.
