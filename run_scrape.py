from src.jobsniffer import scrape_jobs

# Example: get *all* AI Engineer jobs in Bengaluru (last hour, with descriptions)
jobs = scrape_jobs(
    site_name=["linkedin"],   # use the site name string; will use LinkedInFull via mapping
    search_term="AI Engineer",
    location="Bengaluru",
    distance=80,
    hours_old=12,                # last hour
    linkedin_fetch_description=True,   # fetch full description, etc.
    verbose=2,  # debug logging
    results_wanted=200,   # omit or set to None to get every match
)
print(f"Found {len(jobs)} jobs")
print(jobs.head())

# Save to JSON
jobs.to_json('scraped_jobs.json', orient='records', date_format='iso')
print("Results saved to scraped_jobs.json")