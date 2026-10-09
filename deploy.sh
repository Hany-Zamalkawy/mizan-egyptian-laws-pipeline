#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Automated GCP VM Setup for the Egypt Legal Data Pipeline
#
# Tested on: Ubuntu 22.04 LTS (GCP Compute Engine e2-standard-4 or larger)
#
# Usage:
#   chmod +x deploy.sh
#   sudo ./deploy.sh
#
# Before running:
#   1. Upload your GCP Service Account JSON key to the VM:
#        gcloud compute scp gcp_key.json <VM_NAME>:~/egypt_pipeline/gcp_key.json
#   2. Set environment variables in /etc/environment or update the .env
#      section below to match your project values.
#
# What this script does:
#   1.  System update & required package install
#   2.  PostgreSQL client + server setup
#   3.  Python 3.10 + pip + virtualenv
#   4.  OCR tools (Tesseract + Arabic language pack + poppler)
#   5.  Clone / copy pipeline code
#   6.  Python dependency install
#   7.  PostgreSQL database & schema initialisation
#   8.  Airflow init (SQLite metadata DB → swap to Postgres in production)
#   9.  Airflow webserver + scheduler as systemd services
#   10. Start services
# =============================================================================

set -euo pipefail   # Exit on error, unset variable, pipe failure
IFS=$'\n\t'

# ─── Configurable Variables ────────────────────────────────────────────────────
PIPELINE_DIR="/opt/egypt_pipeline"
VENV_DIR="${PIPELINE_DIR}/venv"
AIRFLOW_HOME="/opt/airflow"
AIRFLOW_VERSION="2.9.1"
PYTHON_VERSION="3.10"
PG_DB="egypt_laws"
PG_USER="pipeline_user"
PG_PASSWORD="$(openssl rand -base64 20)"   # generated on first run
ALERT_EMAIL="devops@example.com"
GCP_PROJECT_ID="your-gcp-project-id"
GCS_RAW_BUCKET="egypt-laws-raw-data"
ADMIN_USER="admin"
ADMIN_PASSWORD="$(openssl rand -base64 16)"
ADMIN_EMAIL="admin@example.com"

# ─── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ─── Require root ─────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Run as root: sudo ./deploy.sh"

info "═══════════════════════════════════════════════"
info " Egypt Legal Data Pipeline — VM Setup"
info "═══════════════════════════════════════════════"

# =============================================================================
# STEP 1 — System update
# =============================================================================
info "Step 1/10: Updating system packages…"
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    curl wget git unzip software-properties-common \
    build-essential libssl-dev libffi-dev \
    libpq-dev \
    ca-certificates gnupg lsb-release

# =============================================================================
# STEP 2 — Python 3.10
# =============================================================================
info "Step 2/10: Installing Python ${PYTHON_VERSION}…"
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq
apt-get install -y -qq \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-dev \
    python${PYTHON_VERSION}-venv \
    python3-pip

# Make python3 → python3.10
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1
python3 --version

# =============================================================================
# STEP 3 — OCR & PDF tools
# =============================================================================
info "Step 3/10: Installing Tesseract OCR + Arabic language pack + poppler…"
apt-get install -y -qq \
    tesseract-ocr \
    tesseract-ocr-ara \
    libtesseract-dev \
    poppler-utils \
    libpoppler-dev

tesseract --version | head -1

# =============================================================================
# STEP 4 — PostgreSQL
# =============================================================================
info "Step 4/10: Installing PostgreSQL 15…"

# Add official PostgreSQL apt repository
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg
echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
apt-get update -qq
apt-get install -y -qq postgresql-15 postgresql-client-15 postgresql-15-pgvector || \
    apt-get install -y -qq postgresql-15 postgresql-client-15   # pgvector optional

systemctl enable postgresql
systemctl start  postgresql

# Create DB user and database
info "Step 4b: Creating PostgreSQL user '${PG_USER}' and database '${PG_DB}'…"
sudo -u postgres psql -c "
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${PG_USER}') THEN
            CREATE USER ${PG_USER} WITH PASSWORD '${PG_PASSWORD}';
        END IF;
    END \$\$;
" 2>/dev/null || true

sudo -u postgres psql -c "
    SELECT 'CREATE DATABASE ${PG_DB} OWNER ${PG_USER}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${PG_DB}')
" | sudo -u postgres psql 2>/dev/null || true

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${PG_DB} TO ${PG_USER};" 2>/dev/null || true

# =============================================================================
# STEP 5 — Pipeline directory & code
# =============================================================================
info "Step 5/10: Setting up pipeline directory at ${PIPELINE_DIR}…"
mkdir -p "${PIPELINE_DIR}"/{output,cache,logs}
mkdir -p "${PIPELINE_DIR}/dags"

# If running from the repo, copy files; else clone from Git.
# Adjust REPO_URL if using a private repository.
REPO_URL="${REPO_URL:-}"   # e.g. https://github.com/org/egypt-laws-pipeline.git

if [[ -n "${REPO_URL}" ]]; then
    info "Cloning repository from ${REPO_URL}…"
    git clone "${REPO_URL}" "${PIPELINE_DIR}/repo"
    cp -r "${PIPELINE_DIR}/repo/"* "${PIPELINE_DIR}/"
else
    warn "REPO_URL not set. Copy your pipeline files manually to ${PIPELINE_DIR}/"
    warn "Required files: config.py scraper.py gcp_storage.py db_loader.py schema.sql"
    warn "                dags/egypt_laws_dag.py requirements.txt gcp_key.json"
fi

# =============================================================================
# STEP 6 — .env file
# =============================================================================
info "Step 6/10: Writing .env file…"
cat > "${PIPELINE_DIR}/.env" <<EOF
# ── GCP ──────────────────────────────────────────────────────
GCP_KEY_PATH=${PIPELINE_DIR}/gcp_key.json
GCP_PROJECT_ID=${GCP_PROJECT_ID}
GCS_RAW_BUCKET=${GCS_RAW_BUCKET}

# ── PostgreSQL ────────────────────────────────────────────────
PG_HOST=localhost
PG_PORT=5432
PG_DB=${PG_DB}
PG_USER=${PG_USER}
PG_PASSWORD=${PG_PASSWORD}

# ── Airflow ───────────────────────────────────────────────────
AIRFLOW_HOME=${AIRFLOW_HOME}
ALERT_EMAIL=${ALERT_EMAIL}
PIPELINE_OUTPUT_DIR=${PIPELINE_DIR}/output

# ── Logging ───────────────────────────────────────────────────
LOG_LEVEL=INFO
EOF
chmod 600 "${PIPELINE_DIR}/.env"

# =============================================================================
# STEP 7 — Python virtualenv & dependencies
# =============================================================================
info "Step 7/10: Creating virtualenv and installing Python dependencies…"
python${PYTHON_VERSION} -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"

pip install --upgrade pip wheel setuptools

# Install Airflow with its constraint file (mandatory to avoid dependency hell)
CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"
pip install "apache-airflow==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}" --quiet

# Install project dependencies (minus airflow — already installed)
pip install \
    httpx[http2] \
    requests \
    beautifulsoup4 \
    lxml \
    playwright \
    pdfplumber \
    PyMuPDF \
    pytesseract \
    Pillow \
    google-cloud-storage \
    psycopg2-binary \
    SQLAlchemy \
    python-dotenv \
    tenacity \
    fake-useragent \
    tqdm \
    pandas \
    openpyxl \
    --quiet

# Install Playwright browsers
playwright install chromium

deactivate

# =============================================================================
# STEP 8 — PostgreSQL Schema Initialisation
# =============================================================================
info "Step 8/10: Initialising PostgreSQL schema…"
PGPASSWORD="${PG_PASSWORD}" psql \
    -h localhost -U "${PG_USER}" -d "${PG_DB}" \
    -f "${PIPELINE_DIR}/schema.sql" \
    -v ON_ERROR_STOP=1 \
    2>&1 | tail -20

# =============================================================================
# STEP 9 — Airflow Initialisation
# =============================================================================
info "Step 9/10: Initialising Apache Airflow…"
export AIRFLOW_HOME="${AIRFLOW_HOME}"
mkdir -p "${AIRFLOW_HOME}"

# Set Airflow to use PostgreSQL as its metadata database
AIRFLOW_DB_URI="postgresql+psycopg2://${PG_USER}:${PG_PASSWORD}@localhost:5432/${PG_DB}_airflow"

# Create Airflow metadata DB
sudo -u postgres psql -c "
    SELECT 'CREATE DATABASE ${PG_DB}_airflow OWNER ${PG_USER}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${PG_DB}_airflow')
" | sudo -u postgres psql 2>/dev/null || true

# Write airflow.cfg overrides via env vars (cleaner than patching cfg)
cat >> /etc/environment <<EOF
AIRFLOW__CORE__EXECUTOR=LocalExecutor
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=${AIRFLOW_DB_URI}
AIRFLOW__CORE__LOAD_EXAMPLES=False
AIRFLOW__CORE__DAGS_FOLDER=${PIPELINE_DIR}/dags
AIRFLOW__WEBSERVER__WEB_SERVER_PORT=8080
AIRFLOW__EMAIL__EMAIL_BACKEND=airflow.utils.email.send_email_smtp
AIRFLOW__SMTP__SMTP_HOST=smtp.gmail.com
AIRFLOW__SMTP__SMTP_PORT=587
AIRFLOW__SMTP__SMTP_STARTTLS=True
AIRFLOW__SMTP__SMTP_MAIL_FROM=${ALERT_EMAIL}
AIRFLOW_HOME=${AIRFLOW_HOME}
EOF

source /etc/environment

# Initialise Airflow DB
source "${VENV_DIR}/bin/activate"
airflow db init

# Create admin user
airflow users create \
    --username  "${ADMIN_USER}" \
    --firstname "Pipeline" \
    --lastname  "Admin" \
    --role      "Admin" \
    --email     "${ADMIN_EMAIL}" \
    --password  "${ADMIN_PASSWORD}"

deactivate

# =============================================================================
# STEP 10 — systemd service files
# =============================================================================
info "Step 10/10: Creating systemd services for Airflow webserver and scheduler…"

# ── Airflow Webserver ──────────────────────────────────────────────────────
cat > /etc/systemd/system/airflow-webserver.service <<EOF
[Unit]
Description=Apache Airflow Webserver
After=network.target postgresql.service
Requires=postgresql.service

[Service]
EnvironmentFile=/etc/environment
User=root
Group=root
Type=simple
ExecStart=${VENV_DIR}/bin/airflow webserver --port 8080
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=airflow-webserver

[Install]
WantedBy=multi-user.target
EOF

# ── Airflow Scheduler ─────────────────────────────────────────────────────
cat > /etc/systemd/system/airflow-scheduler.service <<EOF
[Unit]
Description=Apache Airflow Scheduler
After=network.target postgresql.service airflow-webserver.service
Requires=postgresql.service

[Service]
EnvironmentFile=/etc/environment
User=root
Group=root
Type=simple
ExecStart=${VENV_DIR}/bin/airflow scheduler
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=airflow-scheduler

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable  airflow-webserver airflow-scheduler
systemctl start   airflow-webserver airflow-scheduler

# =============================================================================
# DONE — Print summary
# =============================================================================
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       Egypt Legal Data Pipeline — Deployment Complete        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${YELLOW}Pipeline dir :${NC} ${PIPELINE_DIR}"
echo -e "  ${YELLOW}Airflow home :${NC} ${AIRFLOW_HOME}"
echo -e "  ${YELLOW}Virtualenv   :${NC} ${VENV_DIR}"
echo ""
echo -e "  ${YELLOW}Airflow UI   :${NC} http://<VM_EXTERNAL_IP>:8080"
echo -e "  ${YELLOW}Admin user   :${NC} ${ADMIN_USER}"
echo -e "  ${YELLOW}Admin pass   :${NC} ${ADMIN_PASSWORD}"
echo ""
echo -e "  ${YELLOW}PostgreSQL   :${NC} host=localhost db=${PG_DB} user=${PG_USER}"
echo -e "  ${YELLOW}PG password  :${NC} ${PG_PASSWORD}"
echo ""
echo -e "  ${RED}IMPORTANT: Save the passwords above — they won't be shown again.${NC}"
echo -e "  ${RED}Place your gcp_key.json at: ${PIPELINE_DIR}/gcp_key.json${NC}"
echo ""
echo -e "  Verify services:"
echo -e "    systemctl status airflow-webserver"
echo -e "    systemctl status airflow-scheduler"
echo -e "    systemctl status postgresql"
echo ""
