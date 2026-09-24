# Social Viral Radar

Streamlit dashboard for discovering popular Instagram Reels with Apify's Instagram Search Scraper and ranking them with an internal Viral Score.

## Features
- General Viral Dashboard with broad categories
- Niche keyword explorer
- Views, likes, comments, age and estimated views/hour
- Viral / Rising / Fresh signals
- 30-minute API-result cache to reduce repeated Apify usage
- API token kept in Streamlit Secrets

## Deploy on Streamlit Community Cloud
1. Upload these files to the root of your GitHub repository.
2. In Streamlit Community Cloud, create an app from the repository and select `app.py`.
3. In app settings > Secrets, add:

```toml
APIFY_API_TOKEN = "your-real-token"
```

4. Deploy/reboot the app.

Do not commit your real API token to GitHub.

## Current limitation
Apify's `Search popular reels` input does not expose a reliable country filter. The dashboard therefore labels this source as global. India-specific discovery and Facebook can be added as separate providers later.
