# Data curation log — 2026-11-22 (Traffic Law amendment)

## Priority and scope

Continued at priority 14, Traffic Law 66/1973. Added selected summaries for Article 3 bis and Article 65, paragraphs and inserted provisions under Articles 65 bis 1 and 65 bis 2. The focus is the high-frequency question of an allegedly abandoned or neglected vehicle being removed: statutory definitions, removal and deposit, notice to a known owner, and the owner's 60-day retrieval/relinquishment routes.

Added a separate relationship record for Amendment Law 17/2024. It records publication in Official Gazette issue 13 bis (A) on 28 March 2024, commencement on 29 March 2024, the amended and inserted provisions, and the two-month transitional obligation. The log explicitly flags that transitional period as expired; it is not offered as a current action window.

## Source hierarchy and limits

- The Supreme Court of Cassation legislation index is the government reference for the amendment's identity and publication metadata.
- The full amendment text is available as an Official Gazette copy hosted by Manshurat, marked as a secondary host rather than a government publisher.
- The State Information Service announcement is used only to explain the amendment's policy context, not to substitute for statutory text.
- Local authorities set the designated storage locations and applicable removal, deposit, and daily storage costs. The summaries do not estimate fees or decide whether a specific car satisfies the statutory definition.
- Each article record is a paraphrase. The Official Gazette and applicable executive and local decisions control.

## Files updated

- `Datasets/egypt_citizen_law_seed.jsonl`: four article records plus one amendment relationship record, each with provenance, status, date, and limitations.
- `Datasets/citizen_law_priority_catalog.json`: expanded rank-14 coverage and linked Law 17/2024 amendment status.
- `Datasets/legal_sources_catalog.json`: expanded Ministry of Interior source scope and added the Supreme Court index and government announcement with their authority limits.
- `README.md`: documented coverage, sources, and expired/current deadlines.
- `docs/updates/2026-11-22-traffic-law-amendment.md`: this curation log.

## Data quality review

Parsed the JSONL and both JSON catalogs; checked unique IDs, provenance fields for article records, source registry links, and consistency between priority coverage and inserted records. Temporary files were excluded from version control.
