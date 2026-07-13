# CLAUDE.md — PRISM

This file gives Claude Code persistent context about the project. Keep it at the repo root.

## What PRISM is

PRISM is a multi-modal product-feedback intelligence platform for B2B SaaS product teams.
It ingests user feedback from many channels (App Store, Google Play, Zendesk, Twitter/X,
Intercom, G2, plus audio interview recordings and support-ticket screenshots), runs each
item through a 6-agent pipeline, and produces AI-generated insight reports with priority
scores and anomaly alerts. The goal: turn a flood of scattered feedback into a short,
ranked "here is what your users are actually telling you this week" digest.

## Architecture (the one-item journey)

Raw feedback → Connector (normalise to FeedbackItem) → SQS queue → 6-agent LangGraph
pipeline → Postgres/pgvector → FastAPI → Next.js dashboard.

The 6 agents:
1. Collector   — pure HTTP, no LLM. Polls external APIs on a schedule.
2. Ingestor    — routes by modality. audio→Whisper (ASR), image→BLIP-2 (image-to-text), foreign-language→NLLB translation.
3. Enricher    — GPT-4o-mini structured extraction (feature, intent, entities) + hybrid RAG retrieval.
4. Clusterer   — sentence-transformers embeddings + HDBSCAN clustering. No LLM.
5. Synthesiser — Claude 3.5 Sonnet. The ONLY agent that needs deep reasoning. Writes the insight report.
6. Alerter     — statistical Z-score anomaly detection + Zero-Shot Classification for labelling. No LLM for core logic.

Model-tiering principle: use the cheapest tool that is good enough for each job.
Claude is reserved for synthesis only — never called per-item.

## Tech stack

Backend:      Python 3.12, FastAPI (async), Pydantic v2
Database:     PostgreSQL 16 + pgvector (Vector(384) column for embeddings)
ORM:          SQLAlchemy 2.x (async) + Alembic migrations
Agents:       LangGraph (typed state machine, not a simple chain)
AI models:    Anthropic SDK (Claude), OpenAI SDK (GPT-4o-mini), sentence-transformers, Whisper, BLIP-2
Vector/RAG:   pgvector (semantic) + rank-bm25 locally / OpenSearch in prod (BM25) + cross-encoder reranking
Queue:        SQS (ElasticMQ locally via Docker), boto3
Cache:        Redis
Eval:         RAGAS + custom GPT-as-judge, run in CI (pytest)
LLMOps:       Langfuse (tracing, prompt versioning, cost tracking)
Frontend:     Next.js 14 (App Router) + TypeScript + Tailwind + Recharts (generated separately via Lovable)
Infra:        AWS CDK (TypeScript) — Lambda, Step Functions, SageMaker, RDS, ElastiCache, API Gateway, CloudFront
Local dev:    Docker Compose (postgres, redis, elasticmq)

## Repo layout

```
prism/
  docker-compose.yml
  CLAUDE.md
  backend/
    main.py               # FastAPI app + router registration
    requirements.txt
    .env / .env.example
    alembic.ini
    api/                  # FastAPI routers (one file per resource)
    agents/               # LangGraph nodes, one file per agent
    connectors/           # one file per data source
    models/
      schemas.py          # Pydantic v2 models — the data contracts
    db/
      database.py         # async engine, AsyncSessionFactory, get_db, Base
      models.py           # SQLAlchemy ORM models
      migrations/         # Alembic
    tests/                # pytest, async
  frontend/               # Next.js (added later)
  infra/                  # AWS CDK (added later)
```

## Core data model

`FeedbackItem` (Pydantic) / `FeedbackItemORM` (SQLAlchemy):
id, source, source_id (dedup hash), text, modality (text/audio/image), language,
item_metadata (JSON; note: DB column is `metadata`, Python attr is `item_metadata` because
`metadata` is reserved in SQLAlchemy), created_at, embedding (Vector(384), nullable),
cluster_id (nullable), processed (bool).

`PipelineState`: item_id, current_agent, status, error, started_at, updated_at.
`InsightReport`: id, cluster_id, title, priority (P0-P3), findings[], actions[], item_count, generated_at.

## Conventions — follow these strictly

- Async everywhere. Every DB call, every HTTP call, every agent handler is `async`.
- Type hints on everything. `from __future__ import annotations` at the top of every module.
- Pydantic v2 for all data crossing a boundary (API request/response, agent in/out).
- Deterministic `source_id` = sha256(f"{source}:{app_id}:{review_id}")[:24] for dedup.
- Connectors NEVER call external libs with fragile deps. Prefer `httpx` directly over
  scraper packages. (We already dropped `app-store-scraper` for this reason — it pins an
  ancient urllib3 that breaks on Python 3.12.)
- One responsibility per file. Small modules.
- No tutorial-style inline comments. Comment only to explain WHY, not WHAT.
- Every connector and agent gets a pytest test that mocks the external call.
- Never hardcode secrets. Everything via env vars loaded through `.env` (python-dotenv).
- DATABASE_URL uses plain `postgresql://` — database.py rewrites it to `postgresql+asyncpg://`
  for the async engine. Alembic env.py does the same. Do not put `+asyncpg` in the .env value.

## Local environment facts

- Python managed by pyenv, version 3.12.3, `.python-version` in backend/.
- venv lives at backend/venv.
- Postgres runs in Docker on host port **5433** (5432 is taken by a local Postgres install).
  So DATABASE_URL is `postgresql://prism:prism@localhost:5433/prism`.
- Redis on 6379, ElasticMQ (SQS mock) on 9324.
- Run the API from inside backend/: `uvicorn main:app --reload`.
- Run migrations from inside backend/: `alembic upgrade head`.

## Current status

Week 1. Scaffold + DB + migrations working. App Store connector written (httpx-based).
Next: fix App Store connector returning 0 rows, then build remaining connectors, then agents.

## Definition of done for any task

1. Code runs locally without errors.
2. Has a pytest test that passes.
3. New endpoints appear in /docs and return correct shapes.
4. No secrets committed. requirements.txt updated if deps added.
