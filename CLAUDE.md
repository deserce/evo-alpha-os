# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
请用中文回复

## Project Overview

EvoAlpha OS is a data-driven quantitative investment platform for Alpha opportunity discovery. It combines quantitative analysis, AI-powered insights, and automated reporting to assist investment decision-making.

**Brand**: dlab (Evolution Lab) | **Slogan**: 进化即自由 (Evolution is Freedom)

## Architecture

This is a monorepo with separate Python backend and Next.js frontend:

```
backend/
├── app/                    # Main application modules
│   ├── agents/             # AI agent implementations (simulate investment masters)
│   ├── alpha/              # Alpha opportunity radar (screening, factor calculation)
│   ├── core/               # Core business logic
│   ├── etf/                # ETF all-weather configuration (asset allocation)
│   ├── news/               # News processing (sentiment analysis, event signals)
│   ├── report/             # Report generation (daily reports, research reports)
│   └── scheduler/          # Task scheduling
├── quant_engine/           # Quantitative analysis engine
│   ├── features/           # Feature engineering pipeline
│   ├── pool/               # Stock pool management
│   └── strategies/         # Trading strategies with backtesting
├── data_job/               # Data processing jobs (ETL)
└── scripts/                # Utility scripts

frontend/
└── src/
    ├── app/                # Next.js App Router
    ├── components/         # React components
    └── lib/                # Utility libraries
```

## Key System Components

### Alpha Opportunity Radar
- Quantitative screening for stocks and sectors
- Alpha factor calculation and ranking system
- Located in `backend/app/alpha/`

### AI Analysis Engine
- Agent-based system simulating investment masters
- Natural language processing for market insights
- Located in `backend/app/agents/`

### ETF All-Weather Configuration
- Asset allocation recommendations
- Risk parity and modern portfolio theory
- Located in `backend/app/etf/`

### Report System
- Automated daily investment reports
- In-depth research report generation
- Located in `backend/app/report/`

### Quant Engine
- Feature engineering for market data
- Strategy backtesting framework
- Stock pool management
- Located in `backend/quant_engine/`

## Development Commands

**Note**: This project is in early initialization phase. Commands will be added as the project develops.

### Backend (Python)
```bash
# Setup (to be implemented)
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Run (to be implemented)
python -m uvicorn main:app --reload
```

### Frontend (Next.js)
```bash
# Setup (to be implemented)
cd frontend
npm install

# Development
npm run dev

# Build
npm run build

# Production
npm start
```

### Testing
```bash
# Backend tests (to be implemented)
cd backend && pytest

# Frontend tests (to be implemented)
cd frontend && npm test
```

## Design Patterns

- **Strategy Pattern**: Trading algorithms in `quant_engine/strategies/`
- **Observer Pattern**: Market data updates and event-driven signals
- **Factory Pattern**: Agent creation in `app/agents/`
- **Repository Pattern**: Data access layer for market data

## Data Pipeline

The system uses an ETL pipeline for market data:
1. **Extract**: Data collection from APIs (`data_job/`)
2. **Transform**: Feature engineering (`quant_engine/features/`)
3. **Load**: Database storage and retrieval

## Project Status

**Current Phase**: Early initialization (structure created, implementation pending)

See `PROJECT_PLAN.md` for current progress and next steps.
