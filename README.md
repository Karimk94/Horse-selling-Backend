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

### 3. Environment Variables
Create a `.env` file in the `backend/` directory based on `.env.example`:

```ini
DATABASE_URL=postgresql+asyncpg://user:password@localhost/dbname
SECRET_KEY=your_super_secret_key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket-name
S3_PRESIGNED_EXPIRATION=3600
```

### 4. Run the Server
Start the development server with live reload:
```bash
uvicorn app.main:app --reload
```

The API will be available at: `http://localhost:8000`

## 📚 API Documentation

Once the server is running, you can access the interactive API docs at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
