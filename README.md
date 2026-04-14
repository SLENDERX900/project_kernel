# Project Kernel - ECE Market Intelligence System

![Project Kernel](https://img.shields.io/badge/Project-Kernel-blue?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge)
![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-red?style=for-the-badge)
![Free Tier](https://img.shields.io/badge/Free%20Tier-Only-success?style=for-the-badge)

A comprehensive Early Childhood Education (ECE) market intelligence system for Shah Alam, Malaysia. Discovers, enriches, and analyzes preschool competitors within a 10km radius of Bukit Jelutong.

**🎉 v2.0 Update**: Now runs entirely on free tiers! Removed brittle Playwright headless browsing and expensive Google Maps API dependencies.

## 🎯 Features

- **🔍 Multi-Source Discovery**: Overpass API, Kiddy123 Directory, Foursquare Places API
- **🆓 Free-Tier Verification**: DuckDuckGo Search (primary) + Tavily fallback
- **🗺️ OpenStreetMap Geocoding**: Nominatim reverse geocoding (no API key needed)
- **📈 Competitive Intelligence**: 5-factor threat scoring rubric
- **🗺️ Interactive Mapping**: Real-time threat visualization with Folium
- **📝 Automated Reports**: AI-generated competitive analysis memos
- **🔄 Smart Deduplication**: 75m radius + ECE prefix awareness (Taska/Tadika separation)
- **🎯 Target Analysis**: Brand A vs Brand B competitive positioning
- **✏️ Manual Data Entry**: Edit and import competitor data directly

## 🆓 Free-Tier API Stack

| Service | Purpose | Free Tier |
|---------|---------|-----------|
| **Nominatim** | Geocoding & Reverse Geocoding | Unlimited (rate limited) |
| **DuckDuckGo Search** | Centre Verification | Unlimited (rate limited) |
| **Tavily** | Verification Fallback | 1K calls/month |
| **Foursquare** | Places API | 100K calls/month |
| **SerpAPI** | Optional Search Fallback | 100 calls/month |

**NO Google Maps API** | **NO Paid LLM APIs** | **NO Headless Browsing**

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- (Optional) API keys for Tavily/Foursquare for enhanced features

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/project_kernel.git
cd project_kernel

# Set up environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Configure environment (optional but recommended)
cp .env.example .env
# Edit .env with your API keys (DDGS works without keys!)

# Run the application
streamlit run app.py
```

### Environment Variables (Optional)

Create a `.env` file to enable enhanced features:

```bash
# Optional - for Foursquare Places API
FOURSQUARE_KEY=your_foursquare_key_here

# Optional - for Tavily fallback verification
TAVILY_KEY=your_tavily_key_here

# Optional - for SerpAPI fallback
SERPAPI_KEY=your_serpapi_key_here
```

**Note**: The system works entirely without API keys using Nominatim (OpenStreetMap) and DuckDuckGo Search!

## 📊 System Architecture (v2.0)

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit Dashboard              │
│  ┌─────────────────────────────────────────────┐   │
│  │         Competitive Analysis         │   │
│  │  ┌─────────┐  ┌─────────┐         │   │
│  │  │  Threat  │  │  Memo     │         │   │
│  │  │  Scoring │  │  Builder   │         │   │
│  │  └─────────┘  └─────────┘         │   │
│  └─────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────┐   │
│  │        Data Enrichment Pipeline       │   │
│  │  ┌─────────┐  ┌─────────┐         │   │
│  │  │ Discovery │  │ Enrichment│         │   │
│  │  │ Engine   │  │  Pipeline   │         │   │
│  │  └─────────┘  └─────────┘         │   │
│  └─────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────┐   │
│  │        Free-Tier Services          │   │
│  │  ┌─────────┐  ┌─────────┐         │   │
│  │  │  DDGS   │  │ Nominatim│         │   │
│  │  │ Search  │  │ Geocoding│         │   │
│  │  └─────────┘  └─────────┘         │   │
│  │  ┌─────────┐  ┌─────────┐         │   │
│  │  │  Tavily │  │Foursquare│         │   │
│  │  │Fallback │  │  Places  │         │   │
│  │  └─────────┘  └─────────┘         │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## 🗺️ Geographic Scope

**Primary Target Areas:**
- **Bukit Jelutong**: 3.1022°N, 101.5333°E (Target location)
- **Setia Alam**: 3.0679°N, 101.5175°E  
- **Elmina**: 3.1500°N, 101.4800°E
- **Denai Alam**: 3.1934°N, 101.5251°E
- **Glenmarie**: 3.0833°N, 101.5167°E

**Search Radius:** 10km (adjustable 1-15km)

## 📈 Threat Scoring Rubric

### Scoring Factors (Max 10 points)

1. **Curriculum overlap** (0-3 pts)
   - Cambridge/EYFS: 3 pts
   - Islamic/KSPK: 2 pts  
   - Play-based: 1 pt

2. **Religious orientation** (0-2 pts)
   - Islamic-integrated: 2 pts
   - International: 1 pt

3. **Scale/reach** (0-2 pts)
   - National chain: 2 pts
   - Regional chain: 1 pt

4. **Fee band overlap** (0-2 pts)
   - Brand B band (RM940-1410): 2 pts
   - Brand A band (RM300-700): 1 pt

5. **Neighbourhood proximity** (0-1 pt)
   - Same as target: 1 pt

### Threat Tiers
- **🔴 High**: 7-10 points
- **🟡 Medium**: 4-6 points
- **🟢 Low**: 1-3 points

## 🖥️ Usage

### Web Dashboard
```bash
streamlit run app.py
# Access at http://localhost:8501
```

### Command Line
```bash
# Discover centers
python fetcher.py --address "Bukit Jelutong, Shah Alam" --radius 10

# Enrich data
python enricher.py --input data/raw.csv --output data/master.csv
```

## 🧩 Components

### Data Discovery (fetcher.py)
- Multi-source ECE center discovery (Overpass API, Kiddy123, Foursquare, Google Maps, Tavily, SerpAPI)
- Geographic targeting and filtering (10km radius from Bukit Jelutong)
- Smart duplicate detection with 150m radius (v6.0)
- ECE prefix awareness: Taska/Tadika/Pusat Jagaan separation
- Nominatim geocoding (OpenStreetMap - free tier)
- Multi-factor deduplication: Name + Distance + Address scoring
- Source priority-based merging (Google Maps > Foursquare > Kiddy123 > Overpass > Tavily > SerpAPI)
- Export to CSV format

### Data Enrichment (enricher.py)
- Nominatim geocoding (OpenStreetMap - free, no API key)
- DDGS waterfall verification: DuckDuckGo Search (primary) → Tavily (fallback)
- Malaysian address standardization (Seksyen, Jalan abbreviations, postcode extraction)
- Nominatim reverse geocoding for neighbourhood assignment
- Coordinate-to-address conversion
- Zero-blank data policy with intelligent inference
- Strict identity deduplication: Hard link (phone) + Soft link (200m distance or identical street names)
- Fee, phone, and curriculum extraction from search snippets
- Removed Playwright dependency (v2.0 update)

### Interactive Dashboard (app.py)
- Real-time threat scoring
- Interactive mapping with threat layers
- AI memo generation
- Data management and export
- Manual editing capabilities

## 📊 Data Flow

```
Raw Discovery → ECE Filtering → Address Standardization → 
Nominatim Geocoding → DDGS Verification → 
Smart Deduplication (150m) → Target Radius Filtering → 
Strict Identity Deduplication (200m soft link) → 
Competitive Analysis → Dashboard Display
```

## 🔧 Configuration

### API Services (Free Tier)
- **Nominatim**: Primary geocoding (OpenStreetMap)
- **DuckDuckGo Search**: Centre verification (no API key)
- **Tavily**: Verification fallback (1K calls/month free)
- **Foursquare**: Places API (optional, 100K calls/month free)
- **SerpAPI**: Search fallback (optional, 100 calls/month free)

### Target Brands
- **Brand A**: Islamic-values preschool, RM300-700/mth, national chain
- **Brand B**: Cambridge eco-Islamic campus, RM1,175/mth, premium

## 📋 Output Formats

### Discovered Data
```csv
Brand,Address,Zone,Source,Confidence
"Tadika Ceria","Jalan ABC 1, Shah Alam",Bukit Jelutong,Tavily,85
```

### Enriched Data
```csv
centre_name,official_name,address,neighbourhood,curriculum,religious_orientation,language_medium,scale,fee_fullday_raw,verification_note,enrichment_date
"Tadika Ceria","Tadika Ceria","No. 15, Jalan Bukit Jelutong, Shah Alam",Bukit Jelutong,"[Verified] Montessori","[Verified] Islamic-integrated","[Verified] BM + English","[Verified] Regional Chain","[Verified] RM 450-650/month","2024-04-02 13:00:00"
```

## 🛠️ Development

### Setup Development Environment
```bash
git clone https://github.com/yourusername/project_kernel.git
cd project_kernel
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install pytest black flake8  # Development tools
```

### Running Tests
```bash
python -m pytest tests/
black *.py
flake8 *.py
```

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/project_kernel/issues)
- **Documentation**: See inline code comments and function docstrings
- **Business Inquiries**: Contact Newmoon Capital

---

**Built with ❤️ for Early Childhood Education market intelligence**
