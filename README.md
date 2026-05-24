# 🔮 AI Stock Kundli - Backend API

**Enterprise-Grade Investment Intelligence Engine**

This repository houses the core multi-agent AI system and REST API backend for AI Stock Kundli. It processes NSE/BSE listed companies using multiple cooperative agents to generate high-fidelity, explainable investment intelligence.

---

## 🛠 Tech Stack

*   **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12+)
*   **Database:** PostgreSQL 16 (with [SQLAlchemy ORM](https://www.sqlalchemy.org/))
*   **Cache & Queue:** Redis 7 + [Celery](https://docs.celeryq.dev/) (for asynchronous data ingestion tasks)
*   **Database Migrations:** [Alembic](https://alembic.jpaq.org/)
*   **AI Integration:** LangChain / Google Gemini API (Multi-agent orchestration)
*   **Security:** JWT authentication (OAuth2 with password hashing using bcrypt)

---

## 📁 Repository Structure

```text
backend/
├── alembic/              # Database migration scripts & history
├── app/
│   ├── api/v1/           # API router and endpoints (Auth, Watchlist, Subscriptions, Companies)
│   ├── core/             # Central configurations, security, cache, and database session setup
│   ├── models/           # SQLAlchemy ORM database models
│   ├── schemas/          # Pydantic data validation schemas
│   ├── services/         # Core business logic:
│   │   ├── agent_aggregator.py   # Aggregates multi-agent intelligence
│   │   ├── agent_fundamental.py  # Fundamental research agent
│   │   ├── agent_technical.py    # Technical indicators analyst agent
│   │   ├── agent_news.py         # Sentiment & financial news agent
│   │   └── llm.py                # LLM connectors and prompt logic
│   ├── tasks/            # Celery asynchronous task definitions (Ingestion)
│   └── main.py           # FastAPI entrypoint
├── Dockerfile            # Production-ready docker container config
├── requirements.txt      # Python package dependencies
└── alembic.ini           # Alembic migration configuration
```

---

## 🚀 Getting Started

### Prerequisites
*   Python 3.12+
*   PostgreSQL 16
*   Redis 7

### 1. Installation
Clone the repository and set up a virtual environment:
```bash
python -m venv venv
venv\Scripts\activate       # On Windows
# source venv/bin/activate  # On macOS/Linux

pip install -r requirements.txt
```

### 2. Configuration
Create a `.env` file in the root directory (use your `.env.example` as a template):
```ini
DATABASE_URL=postgresql://user:password@localhost:5432/kundlidb
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=your-jwt-signing-secret
GEMINI_API_KEY=your-gemini-api-key
```

### 3. Run Database Migrations
Initialize your database schemas using Alembic:
```bash
alembic upgrade head
```

### 4. Run the Servers

**Start the FastAPI Web Server:**
```bash
uvicorn app.main:app --reload --port 8000
```
*The web server will run at: `http://localhost:8000`*

**Start the Celery Worker (for Async Data Ingestion):**
```bash
celery -A app.core.celery_app worker --loglevel=info
```

---

## 🔗 Key API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| **GET** | `/api/v1/health` | Service status health check |
| **POST** | `/api/v1/auth/signup` | Register a new user |
| **POST** | `/api/v1/auth/login` | Login and acquire JWT access token |
| **GET** | `/api/v1/auth/me` | Fetch authenticated user details |
| **GET** | `/api/v1/companies/{ticker}/kundli` | Retrieve/Trigger multi-agent AI Kundli report |
| **POST** | `/api/v1/watchlist` | Add/Remove companies to the user's watchlist |

*Interactive Swagger documentation is available out-of-the-box at `http://localhost:8000/docs`.*

---

*Built with ❤️ for Indian investors.*
