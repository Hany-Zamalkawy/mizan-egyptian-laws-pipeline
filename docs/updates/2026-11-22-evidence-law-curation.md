# Data curation log — 2026-11-22 (Evidence Law)

## Priority and scope

Continued the ranked plan at priority 13, Law 25/1968 (Evidence in Civil and Commercial Matters). The existing citizen seed covered burden of proof, private-document signatures, and fixed date. Added Articles 10–13, which explain how official documents are defined and what evidentiary weight may attach to official documents and copies.

## Source path and limitations

- The Ministry of Finance legislation index lists Law 25/1968 as in force and provides a downloadable text. Its notice says the Gazette is the legal reference.
- WIPO Lex provides the Arabic original law text, including Articles 10–13. This is the 1968 enactment, not a consolidated amendment history.
- The Egyptian Notaries Union law page was used as an additional professional cross-check. It is not the Official Gazette.
- Each item is a concise paraphrase, not a verbatim statute quote. Currentness notes preserve the distinction between what an officer personally records and statements supplied by parties, and between original documents and copies.

## Files updated

- `Datasets/egypt_citizen_law_seed.jsonl`: added one record per article, with source URLs, source type, retrieval date, text status, and currentness note.
- `Datasets/citizen_law_priority_catalog.json`: updated rank 13 article coverage and flagged limits of the underlying source copies.
- `Datasets/legal_sources_catalog.json`: added the official government law index and clarified the limits of the original-law WIPO copy.
- `README.md`: noted the new coverage and source limitations.
- `docs/updates/2026-11-22-evidence-law-curation.md`: detailed scope, provenance, and review steps.

## Data quality review

Parsed the JSONL and both catalog JSON files; checked unique record IDs, required provenance fields, article coverage, and the existence of the referenced source records. Temporary files were excluded from version control.
