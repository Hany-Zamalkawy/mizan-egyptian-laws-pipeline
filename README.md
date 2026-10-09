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
