# 🏛️ Egypt Legal Data Pipeline

> **Production-ready end-to-end pipeline** for scraping, parsing, storing, and orchestrating all Egyptian Laws & Decrees into Google Cloud Platform.

---

## 📐 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Apache Airflow DAG (02:00 UTC)                  │
│                                                                       │
│  check_new_laws ──► scrape_and_extract ──► upload_raw_to_gcs         │
│                                                    │                  │
│                                      transform_and_load_postgres      │
│                                                    │                  │
│                                           log_run_stats               │
└─────────────────────────────────────────────────────────────────────┘
           │                    │                         │
    PostgreSQL           Google Cloud Storage        PostgreSQL
    (dedup check)        Bronze Layer                Silver / Gold
                         raw/json/<yr>/<num>.json    laws + articles
                         raw/pdf/<yr>/<num>.pdf      tables
```

---

## 📁 File Structure

```
ميزان/
├── config.py              # Centralised config (reads from .env)
├── scraper.py             # Web scraper: index + detail + PDF downloader
├── gcp_storage.py         # Bronze layer: GCS uploader
├── db_loader.py           # Silver layer: PostgreSQL upsert
├── schema.sql             # DB schema: tables, indexes, FTS, views
├── requirements.txt       # Python dependencies
├── deploy.sh              # One-click GCP VM setup script
├── .env.example           # Environment variable template
├── .gitignore
├── dags/
│   └── egypt_laws_dag.py  # Airflow DAG (5 tasks, daily at 02:00 UTC)
├── cache/                 # Local PDF cache (gitignored)
└── output/                # JSONL output files (gitignored)
```

## Citizen-facing legal data (curated seed)

`Datasets/egypt_citizen_law_seed.jsonl` is an import-ready JSON Lines seed for the citizen guidance/RAG use case. It contains selected source-linked articles and citizen pathways across traffic, work, tenancy, civil contracts, family support, consumer rights, social insurance, child protection, and personal-data protection. The current seed is a curated, prioritized collection, not an exhaustive collection of Egyptian legislation. It now includes concise, source-linked summaries of Articles 6 and 11 bis of Personal Status Law 25/1929 and Article 21 of Law 1/2000, covering harm-based separation, notice and remedies related to a subsequent marriage, and proof/documentation of divorce. It also covers Articles 2 of Law 25/1920 and 18 bis and 18 bis (second) of Law 25/1929 on divorce-related maintenance, mutaa, and child support. Each record carries its source URL, source type, retrieval date, text status, and a note on limits/currentness. The service checklists are guidance grounded in official sources, not definitive legal opinions.

`Datasets/legal_sources_catalog.json` records source provenance and authority tiers. Prefer the Official Gazette and government portals for primary texts. Manshurat entries are marked as secondary repositories and should be checked against the Gazette and amendments before use. The seed deliberately does not treat low-quality OCR, unverified status labels, or scraped administrative decisions as citizen-ready law text. When adding records, keep one article per record, preserve the article number and publication citation, distinguish verbatim text from a paraphrase, and record amendment/currentness checks.

`Datasets/citizen_law_priority_catalog.json` ranks 33 commonly useful laws across family, housing, work, consumer, criminal, court, traffic, insurance, identity, child, tax, and digital topics. The ordering is a product heuristic, not measured search volume. Each entry lists the kinds of questions it may support, a source link, and whether the text still needs amendment consolidation. `metadata_verified_text_not_ingested` means the law's identity/source page was checked but the text is not yet part of the article seed.

The catalog is a prioritized start, not a complete inventory of every Egyptian law. Before expanding into lower-priority statutes, ingest and consolidate the full texts and amendments for these entries. Update this catalog whenever an amendment changes applicability. For example, as of the catalog date, Law 160 of 2026 postponed the effective date of the new Criminal Procedure Law 174 of 2025 to 1 October 2027, so Law 150 of 1950 remains the procedural baseline until then, subject to its own amendments. The amendment appears on the official [Amiri Press legislation portal](https://www.alamiria.com/Sec/TashTxt?id=Aalg37zxdcU%3D); the 2025 law text is available from the [Ministry of Justice Gazette copy](https://moj.gov.eg/backend/api/documents/download/20F3150C-29F1-487E-A0F0-FCCE10CB207B).

---

## 🔑 GCP Service Account Setup

### Step 1 — Create a Service Account

```bash
# Replace with your project ID
export PROJECT_ID="your-gcp-project-id"

gcloud iam service-accounts create egypt-pipeline-sa \
    --display-name="Egypt Laws Pipeline" \
    --project="${PROJECT_ID}"
```

### Step 2 — Grant Required Roles

```bash
SA_EMAIL="egypt-pipeline-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# Cloud Storage admin (upload to bucket)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.admin"

# (Optional) BigQuery for future Gold Layer
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/bigquery.dataEditor"
```

### Step 3 — Download the JSON Key

```bash
gcloud iam service-accounts keys create gcp_key.json \
    --iam-account="${SA_EMAIL}"
```

> **⚠️ Security**: Never commit `gcp_key.json` to Git. It is already listed in `.gitignore`.  
> On a GCP VM, prefer **Application Default Credentials** (no key file needed):  
> ```bash
> gcloud auth application-default login
> ```

### Step 4 — Create the GCS Bucket

```bash
gcloud storage buckets create "gs://egypt-laws-raw-data" \
    --project="${PROJECT_ID}" \
    --location="US" \
    --uniform-bucket-level-access
```

---

## 🚀 GCP VM Deployment

### Prerequisites

- GCP Compute Engine VM: **Ubuntu 22.04 LTS**, `e2-standard-4` (4 vCPU, 16 GB RAM) or larger.
- VM has outbound internet access (for `apt`, `pip`, scraping).
- Port **8080** open in the VM's firewall rule (Airflow UI).

### One-Command Deployment

```bash
# 1. SSH into the VM
gcloud compute ssh <VM_NAME> --zone=<ZONE>

# 2. Upload pipeline files (from your local machine)
gcloud compute scp --recurse ./ميزان/ <VM_NAME>:/tmp/pipeline/ --zone=<ZONE>

# 3. On the VM — copy files and run deploy script
sudo cp -r /tmp/pipeline/* /opt/egypt_pipeline/
sudo chmod +x /opt/egypt_pipeline/deploy.sh
sudo /opt/egypt_pipeline/deploy.sh
```

The script will print **Airflow admin credentials** and **PostgreSQL password** at the end.  
Save them immediately — they are randomly generated and not stored elsewhere.

---

## ⚙️ Local Development Setup

```bash
# 1. Clone and enter project
cd "ميزان/"

# 2. Create virtual environment
python3.10 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your GCP project ID, PostgreSQL credentials, etc.

# 5. Initialise the database
psql -U postgres -c "CREATE DATABASE egypt_laws;"
psql -U postgres -d egypt_laws -f schema.sql

# 6. Run the scraper manually (test with a single year)
python scraper.py --years 2024 --output ./output/laws_2024.jsonl

# 7. Upload to GCS
python -c "
from gcp_storage import upload_laws_to_gcs
import json, pathlib
laws = [json.loads(l) for l in pathlib.Path('output/laws_2024.jsonl').read_text().splitlines() if l]
upload_laws_to_gcs(laws)
"

# 8. Load into PostgreSQL
python -c "
from db_loader import DBLoader
import json, pathlib
laws = [json.loads(l) for l in pathlib.Path('output/laws_2024.jsonl').read_text().splitlines() if l]
with DBLoader() as db:
    db.load_laws(laws)
"
```

---

## 🔄 Airflow DAG Tasks

| Task | Description | Key Config |
|------|-------------|------------|
| `check_new_laws` | Fetches existing `(law_number, year)` keys from PostgreSQL | — |
| `scrape_and_extract` | Runs `scraper.py` for current + previous year | Rate-limited, retried |
| `upload_raw_to_gcs` | Uploads JSON + PDFs to `egypt-laws-raw-data` GCS bucket | Bronze layer |
| `transform_and_load_postgres` | Upserts laws & articles into `laws` + `articles` tables | Silver layer |
| `log_run_stats` | Writes pipeline metrics to `scrape_log` table | Always runs |

**Schedule**: `0 2 * * *` (02:00 AM UTC daily)  
**Retries**: 3 × with 5-minute exponential back-off  
**Failure alert**: Email to `ALERT_EMAIL`

---

## 🗄️ Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `laws` | Master law registry (UUID PK, Arabic FTS on title + body) |
| `articles` | Individual articles (FK → laws, Arabic FTS on content) |
| `scrape_log` | Pipeline run audit trail |

### Full-Text Search Examples

```sql
-- Search laws containing "عمل" (labour)
SELECT law_number, year, title
FROM   laws
WHERE  fts_title || fts_body @@ to_tsquery('simple', 'عمل')
ORDER  BY ts_rank(fts_title || fts_body, to_tsquery('simple', 'عمل')) DESC;

-- Search articles containing "إجازة" (leave)
SELECT l.law_number, l.year, a.article_number, a.content
FROM   articles a JOIN laws l USING (law_id)
WHERE  a.fts_content @@ to_tsquery('simple', 'إجازة')
LIMIT  20;

-- Fuzzy title search (trigram)
SELECT law_number, year, title
FROM   laws
WHERE  title % 'قانون العمل'
ORDER  BY similarity(title, 'قانون العمل') DESC;
```

---

## 🛡️ Security Checklist

- [ ] `gcp_key.json` is **NOT** committed to Git (`.gitignore` enforces this)
- [ ] `.env` is **NOT** committed to Git
- [ ] PostgreSQL is bound to `localhost` only (`listen_addresses = 'localhost'`)
- [ ] Airflow UI port 8080 is protected by VPC firewall (allowlist your IP)
- [ ] Service account has **least-privilege** roles (Storage Admin only, not Project Owner)
- [ ] GCS bucket has **uniform bucket-level access** enabled (no per-object ACLs)
- [ ] Rotate the service account key every 90 days via:
  ```bash
  gcloud iam service-accounts keys create gcp_key_new.json --iam-account=<SA_EMAIL>
  gcloud iam service-accounts keys delete <OLD_KEY_ID> --iam-account=<SA_EMAIL>
  ```

---

## 🔧 Troubleshooting

| Problem | Solution |
|---------|----------|
| `403 Forbidden` from scraper | Playwright fallback activates automatically; check `ScraperConfig.USER_AGENTS` |
| `google.auth.exceptions.DefaultCredentialsError` | Set `GCP_KEY_PATH` in `.env` or run `gcloud auth application-default login` |
| Airflow scheduler not picking up DAG | Confirm `AIRFLOW__CORE__DAGS_FOLDER` points to `dags/` and restart scheduler |
| PostgreSQL `ENUM` error on upsert | Unknown category/status values are mapped to `'أخرى'` / `'ساري'` by `db_loader.py` |
| Tesseract not finding Arabic | Run `tesseract --list-langs` — must include `ara`; reinstall `tesseract-ocr-ara` |

---

## 📊 Monitoring

```bash
# Check Airflow service status
systemctl status airflow-webserver
systemctl status airflow-scheduler

# View scheduler logs
journalctl -u airflow-scheduler -f

# Query pipeline run history
psql -U pipeline_user -d egypt_laws -c "
  SELECT run_date, laws_new, laws_failed, duration_seconds, status
  FROM   scrape_log
  ORDER  BY created_at DESC
  LIMIT  10;
"
```

---

## 📜 License

Internal — DEPI Final Project — Egypt Legal Data Pipeline


Recent expansion added selected provisions from Personal Data Protection Law 151/2020, Social Insurance Law 148/2019, Child Law 12/1996, and a 2023 amendment record. Texts from consolidated secondary repositories are explicitly labeled and require Gazette cross-check before legal reliance. The ranking is an editorial prioritization for the project, not measured search-volume data.



The latest tranche adds selected articles on khulʿ proceedings, family dispute settlement, civil-record corrections, online privacy violations, and building permits. These are paraphrases linked to legal repositories and corroborating legal references; verify the Gazette text, amendments, and applicable regulations before legal use.



The 2026-10-16 expansion adds five records: Evidence Law 25/1968 Article 1, Civil and Commercial Procedure Law 13/1968 Article 63, and official amendment/correction relationships for Income Tax Law 91/2005 and VAT Law 67/2016 (including the ETA-listed 2026 correction for Laws 149/150/151). The source catalog now includes ETA tax indexes and legal text cross-check sources. Amendment relationships are intentionally not presented as consolidated legal advice.


Expansion (2026-10-16): added citizen-focused Civil Code provisions 148-149 and refreshed the existing Article 147 source to a government-hosted copy (contracts and adhesion), Consumer Protection Law Article 17 (return right with exceptions), and an official-source relationship record for Labor Law 14/2025. Each item records source tier, text status, retrieval date, and a currentness caveat.



Expansion (2026-10-16): added Labor Law 14/2025 Articles 54, 99, 124, and 128, covering maternity leave, new work arrangements, annual leave, and casual/new-child leave; added Consumer Protection Law Article 21 on defective or nonconforming goods. Sources include the Gazette copy hosted by ETA and the Consumer Protection Agency law index.


Expansion (2026-10-16): added Civil Code Articles 558, 563, and 564 on lease definition, unspecified lease term/notice, and delivery condition, plus Consumer Protection Law Article 10 on invoices. Lease summaries explicitly distinguish Civil Code rules from special rent legislation; Consumer Protection sources now include WIPO Lex and the Consumer Protection Agency.


Expansion (2026-10-16): added Civil Code Articles 568-570 on landlord repairs, damage to rented premises, and urgent repairs, plus Consumer Protection Law Article 22 on durable-goods warranties. These summaries retain conditions and caution against automatic rent deductions or warranty assumptions.


Expansion (2026-10-16): added Evidence Law Articles 14 and 15 on private-document signatures and fixed dates, and Family Courts Law Article 7 on the settlement process and its time limit. Texts are paraphrased and identify the source tier and procedural caveats.


Expansion (2026-11-11): added Civil Code Article 157 on remedies for nonperformance in bilateral contracts, and Consumer Protection Law Articles 30-31 on installment disclosures and early settlement. Consumer entries are paraphrases from a secondary consolidated repository with WIPO Lex cross-reference; verify amendments and implementing rules before case-specific use.


Expansion (2026-11-11): added Article 1 of Law 4/1996 on the scope of civil-law tenancy rules and Article 24 of Criminal Procedure Law 150/1950 on receiving and forwarding criminal reports. The criminal-procedure record is explicitly transitional until 1 October 2027 under Law 160/2026; check special rent rules and amendments for every case.
