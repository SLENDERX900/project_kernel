# Project Kestrel v2.0 — Refactoring Summary

## Architecture Changes

### Files Deleted
- `playwright_verifier.py` — Headless browsing too brittle, completely removed
- All v2.0 draft files (dashboard.py v1, fetcher_v2.py, etc.)

### Files Modified

#### 1. `fetcher.py`
**Changes:**
- **Deduplication radius**: Changed from 10m to 75m (sensible urban buffer)
- **ECE prefix awareness**: Added smart fuzzy matching for Taska/Tadika/Pusat Jagaan
  - `extract_name_components()` — extracts prefix, brand, location
  - `calculate_name_similarity()` — applies ECE prefix penalty (-15% for different types)
  - Branch separation preserved: "Real Kids Setia Alam" vs "Real Kids Bukit Jelutong" stay separate
- **Geocoding**: Removed Google Maps API, using Nominatim only
  - `geocode_address()` — now Nominatim-only
- **Enhanced deduplication logic**:
  - `should_merge_records()` — multi-factor decision engine
  - Name + Distance + Address combined scoring
  - Transparent merge reason logging

#### 2. `enricher.py`
**Changes:**
- **Removed Playwright**: All Playwright references removed
- **Added DDGS waterfall verification**:
  - `verify_with_ddgs()` — Primary verification using DuckDuckGo Search
  - `verify_with_tavily()` — Fallback using Tavily API
  - `verify_centre_waterfall()` — Orchestrates DDGS → Tavily fallback
  - `extract_fees_from_text()` — Regex extraction of RM patterns
  - `extract_phone_from_text()` — Malaysian phone number extraction
  - `extract_curriculum_from_text()` — Curriculum keyword matching
- **Nominatim reverse geocoding**:
  - `reverse_geocode_nominatim()` — Free tier reverse geocoding
  - `assign_neighbourhood()` — Now uses Nominatim as Stage 1
- **Malaysian address standardization**:
  - `standardize_malaysian_address()` — Handles Seksyen, Jalan abbreviations
  - `MALAYSIAN_ABBREVS` — Dictionary of common abbreviations
  - `extract_postcode()` — 5-digit postcode extraction
- **Geocoding**: Removed Google Maps, Nominatim only
  - `geocode_with_fallback()` — Nominatim-only with rate limiting
- **Deduplication**: Updated radius to 75m in `deduplicate_enriched()`
- **Verification integration**: Step 2.5 now uses DDGS waterfall instead of Playwright

#### 3. `requirements.txt`
**Changes:**
- Added `duckduckgo-search>=3.9` — Free search API
- Removed `openai`, `anthropic` — No paid LLM APIs
- Removed `playwright`, `playwright-stealth` — No headless browsing
- Kept: `streamlit`, `pandas`, `folium`, `geopy`, `thefuzz`, `tavily-python`

#### 4. `dashboard.py` (New File)
**Features:**
- Unified Streamlit dashboard
- Pipeline Control: Fetch → Enrich with progress tracking
- Data Explorer: Filter by neighbourhood, religion, scale
- Map View: Interactive folium map (Nominatim-based)
- Settings: API key configuration for free-tier services
- No Google Maps dependencies

## Key Technical Improvements

### 1. Smart Deduplication v2.0
```python
DEDUP_RADIUS_METERS = 75  # Was 10m - too strict
ECE_PREFIX_PENALTY = 15   # Different prefixes = different centres

# Example:
# "Tadika Sri Sinar" vs "Taska Sri Sinar" = 85 - 15 = 70% (NOT merged)
# "Real Kids Setia Alam" vs "Real Kids Setia Alam" = 95% (merged)
# "Real Kids Setia Alam" vs "Real Kids Bukit Jelutong" = 72% (NOT merged - different locations)
```

### 2. Waterfall Verification
```python
# Primary: DDGS (Free, no API key)
verify_with_ddgs(centre_name, neighbourhood)
# - Runs targeted queries: "{name}" {neighbourhood} yuran OR fees "RM"
# - Aggregates top 5 result snippets
# - Extracts fees, phone, curriculum via regex
# - time.sleep(1.5) between queries for rate limits

# Fallback: Tavily (Free tier available)
verify_with_tavily(centre_name, neighbourhood)
# - Only used if DDGS insufficient
# - Saves API credits
```

### 3. Nominatim-Only Geospatial
```python
# Removed: Google Maps Geocoding API
# Primary: Nominatim (OpenStreetMap) - FREE

def geocode_address(address):
    # Uses Nominatim with 1.1s rate limiting
    # Returns lat, lng, success_flag
    
def reverse_geocode_nominatim(lat, lng):
    # Returns neighbourhood, city, postcode, state
    # Used for precise neighbourhood assignment
```

### 4. Malaysian Address Standardization
```python
MALAYSIAN_ABBREVS = {
    "jln": "jalan",
    "pers": "persiaran",
    "lor": "lorong",
    "sck": "seksyen",
    # ... etc
}

def standardize_malaysian_address(address):
    # Expands abbreviations
    # Normalizes Seksyen variations
    # Ensures Selangor suffix
    # Cleans up commas and whitespace
```

## Free-Tier API Usage

| Service | Type | Free Tier | Usage |
|---------|------|-----------|-------|
| Nominatim | Geocoding | Unlimited (with rate limits) | Primary geocoding |
| DDGS | Search | Unlimited (with rate limits) | Primary verification |
| Tavily | Search | 1K calls/month | Fallback verification |
| Foursquare | Places | 100K calls/month | Data fetching |
| SerpAPI | Search | 100 calls/month | Optional fallback |

**NO Google Maps API, NO paid OpenAI/Anthropic APIs, NO Playwright**

## Running the Pipeline

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Configure API Keys (Optional but Recommended)
Create `.env`:
```
FOURSQUARE_KEY=your_key_here
TAVILY_KEY=your_key_here
SERPAPI_KEY=your_key_here  # Optional
```

Note: DDGS and Nominatim work without API keys!

### Run Dashboard
```bash
streamlit run dashboard.py
```

### Or Run Components Individually
```bash
# Fetch data
python fetcher.py --radius 10 --output data/raw.csv

# Enrich data (includes DDGS verification)
python enricher.py --input data/raw.csv --output data/master.csv
```

## Verification Flow

1. **Fetch**: Collect from Overpass, Kiddy123, Foursquare
2. **Deduplicate**: Smart merging with 75m radius, ECE prefix awareness
3. **Geocode**: Nominatim-only for missing coordinates
4. **Enrich**: 
   - Assign neighbourhood (Nominatim reverse geocode → bounding box → keywords)
   - Standardize Malaysian addresses
   - Classify religion, scale, curriculum
   - Infer fees
5. **Verify** (DDGS Waterfall):
   - Run DDGS search queries
   - Extract fees/phone/curriculum from snippets
   - If insufficient → Tavily fallback
   - Update records with verified data
6. **Deduplicate again**: Post-enrichment merge check
7. **Export**: master.csv ready for analysis

## Performance Notes

### Rate Limiting
- Nominatim: 1.1s delay between requests
- DDGS: 1.5s delay between queries
- Tavily: Respected via API limits

### Memory Management
- Processes records in batches
- Lazy loading in dashboard
- CSV-based data persistence

### Error Handling
- Graceful degradation if APIs fail
- Falls back to inferred data
- Continues pipeline on individual record failures

## Testing the Changes

```bash
# Test deduplication
python -c "
from fetcher import calculate_name_similarity
sim, meta = calculate_name_similarity('Tadika ABC Setia Alam', 'Taska ABC Setia Alam')
print(f'Similarity: {sim}% (should be ~70 with penalty)')
print(f'Prefixes: {meta[\"prefix1\"]} vs {meta[\"prefix2\"]}')
"

# Test address standardization
python -c "
from enricher import standardize_malaysian_address
print(standardize_malaysian_address('No 5, Jln Teknologi, Sek 3, Shah Alam'))
"

# Test DDGS availability
python -c "
from enricher import DDGS_AVAILABLE
print(f'DDGS Available: {DDGS_AVAILABLE}')
"
```

## Migration Checklist

- [x] Delete playwright_verifier.py
- [x] Update requirements.txt
- [x] Modify fetcher.py (deduplication, Nominatim geocoding)
- [x] Modify enricher.py (DDGS verification, Nominatim geocoding, address standardization)
- [x] Create dashboard.py
- [ ] Test fetcher.py: `python fetcher.py --radius 5 --output test_raw.csv`
- [ ] Test enricher.py: `python enricher.py --input test_raw.csv --output test_master.csv`
- [ ] Test dashboard: `streamlit run dashboard.py`
- [ ] Verify DDGS working: Check logs for "DDGS: X snippets, Y fees found"
- [ ] Verify Nominatim working: Check logs for "[Nominatim] Geocoded: ..."
- [ ] Check deduplication: Review merge logs for correct branch separation

## Known Limitations

1. **Nominatim Rate Limits**: If geocoding many addresses, may hit rate limits. Solution: Process in smaller batches.

2. **DDGS Results Quality**: DDGS may return fewer results than Google Search. Solution: Tavily fallback for critical records.

3. **Address Coverage**: Some Malaysian addresses may not be in OpenStreetMap. Solution: Manual verification for missing addresses.

4. **No Real-Time Map**: Map uses pre-geocoded coordinates, not live geocoding.

## Next Steps

1. Test the full pipeline with a small radius (5km)
2. Review deduplication logs for any incorrect merges
3. Verify DDGS is extracting fees correctly
4. Check Nominatim neighbourhood assignments
5. Once stable, increase radius to 10km for full coverage

---

**Version**: 2.0  
**Date**: 2024  
**Status**: Ready for Testing
