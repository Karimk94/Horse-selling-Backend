# Horse Marketplace API (Backend)

A high-performance asynchronous REST API built with **FastAPI**, **PostgreSQL**, and **AWS S3**.

## 🚀 Features

- **Authentication**: securely handle user signup and login with JWT (JSON Web Tokens).
- **Horse Listings**: Create, read, and filter horse listings with ease.
- **Media Uploads**: Direct-to-S3 file uploads using secure presigned URLs.
- **Async Database**: Fully asynchronous database operations using `SQLAlchemy` and `asyncpg`.
- **Automatic Docs**: Interactive API documentation via Swagger UI.

## 🛠 Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL
- **ORM**: SQLAlchemy (Async)
- **Storage**: AWS S3 (via `boto3`)
- **Validation**: Pydantic

## 📦 Installation & Setup

### 1. Prerequisites
- Python 3.10+
- PostgreSQL installed and running
- An AWS Account with an S3 bucket

### 2. Clone & Configure
Navigate to the backend directory:
```bash
cd backend
```

Create a virtual environment (optional but recommended):
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Install development dependencies when working on tests or migrations:
```bash
pip install -r requirements-dev.txt
```

### 3. Environment Variables
Copy `.env.example` to `.env` in the `Backend/` directory and adjust values for your environment:

```bash
copy .env.example .env
```

Example contents:

```ini
DATABASE_URL=postgresql+asyncpg://user:password@localhost/dbname
TEST_DATABASE_URL=postgresql+asyncpg://user:password@localhost/dbname_test
AUTO_CREATE_SCHEMA=false
SECRET_KEY=your_super_secret_key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
SOFT_DELETE_RESTORE_DAYS=30
PURGE_CONFIRM_TOKEN=PURGE

# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket-name
S3_PRESIGNED_EXPIRATION=3600
```

### 4. Run the Server
Apply migrations first:
```bash
alembic upgrade head
```

Start the development server with live reload:
```bash
uvicorn app.main:app --reload
```

The API will be available at: `http://localhost:8000`

`AUTO_CREATE_SCHEMA` is disabled by default so runtime schema management stays Alembic-driven. If you need the old startup `create_all` behavior temporarily for local experimentation, set `AUTO_CREATE_SCHEMA=true`.

### 5. Run Tests
For DB-backed integration tests, configure `TEST_DATABASE_URL` to a dedicated disposable database.

Run the backend test suite:
```bash
pytest -q
```

### 6. Run Migrations
Create or update the database schema with Alembic:
```bash
alembic upgrade head
```

If you already have an existing database created from `Base.metadata.create_all`, align Alembic without reapplying the baseline using:
```bash
alembic stamp head
```

### 7. CI
The backend repo includes a GitHub Actions workflow at `.github/workflows/backend-ci.yml` that:
- starts PostgreSQL
- installs runtime and development dependencies
- runs a dedicated migration smoke job:
	- `alembic upgrade head` on a clean database
	- `alembic downgrade base`
	- `alembic upgrade head` again
- runs `alembic upgrade head`
- runs `pytest` with a coverage gate (`--cov-fail-under=45` from `pytest.ini`)
- uploads `coverage.xml` as a workflow artifact

### 8. Admin Purge Confirmation
Destructive purge endpoints require an explicit confirmation token. The token is configured via `PURGE_CONFIRM_TOKEN` (default: `PURGE`).

- Missing `confirm_token` returns `422` (validation error)
- Invalid `confirm_token` returns `400` (`Invalid confirmation token`)

Manual expired purge (query param required):
```bash
curl -X DELETE "http://localhost:8000/api/v1/admin/listings/deleted/expired?confirm_token=PURGE" \
	-H "Authorization: Bearer <admin-token>"
```

Bulk purge (request body field required):
```bash
curl -X POST "http://localhost:8000/api/v1/admin/listings/bulk/purge" \
	-H "Authorization: Bearer <admin-token>" \
	-H "Content-Type: application/json" \
	-d '{
		"horse_ids": ["<horse-uuid-1>", "<horse-uuid-2>"],
		"confirm_token": "PURGE"
	}'
```

Non-sensitive security posture flags for admins:
```bash
curl -X GET "http://localhost:8000/api/v1/admin/security/status" \
	-H "Authorization: Bearer <admin-token>"
```

Example response:
```json
{
	"purge_confirm_token_strong": true,
	"expiry_purge_enabled": true,
	"restore_window_days": 30
}
```

### 9. Purge Safety Model
The purge flow is protected by multiple layers across backend and mobile.

Backend protections:
- `confirm_token` is required for destructive purge endpoints:
	- Manual purge: query param `confirm_token`
	- Bulk purge: body field `confirm_token`
- Missing token -> `422` validation error
- Invalid token -> `400` with `Invalid confirmation token`
- Purge token is environment-configurable with `PURGE_CONFIRM_TOKEN` (default `PURGE`)
- Startup warning logs if purge token is weak/default (`PURGE_TOKEN_WEAK_WARNING`)
- `GET /api/v1/admin/security/status` exposes non-sensitive posture flags only

Mobile protections:
- Admin UI requires typing `PURGE` before manual or bulk purge executes
- Security-status card shows token strength and expiry-purge mode
- If security-status endpoint is unavailable, admin panel still loads with safe fallback behavior

Test coverage highlights:
- Backend behavior + negative paths for purge token requirements
- Backend non-leakage + OpenAPI safe-schema assertions for security-status endpoint
- Backend access-control checks (`401` unauthenticated, `403` non-admin) for security-status endpoint
- Mobile screen tests for typed confirmation (manual + bulk) and security-status rendering/fallback

## 📚 API Documentation

Once the server is running, you can access the interactive API docs at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
