# Debt Tracker App

A personal app to track credit card debt and calculate payoff strategies.

## Features

- Track multiple credit cards (name, balance, interest rate, minimum payment)
- Avalanche method (highest interest first) 
- Snowball method (smallest balance first)
- Progress tracking over time
- Monthly payment tracking

## Tech Stack

- Python/Flask
- SQLite (local storage)
- Chart.js for visualizations

## Getting Started

1. Create virtual environment: `python3 -m venv venv`
2. Activate: `source venv/bin/activate`
3. Install: `pip install -r requirements.txt`
4. Run: `python app.py`
5. Open http://localhost:5000

## Data

All data is stored locally in `debt.db`. No external services.

## Security

- Store locally only
- No bank credentials
- Manual entry for safety
