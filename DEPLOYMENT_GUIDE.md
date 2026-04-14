# Project Kestrel - ECE Market Intelligence System

## 🎯 Overview
Project Kestrel is a comprehensive Early Childhood Education (ECE) market intelligence system for Shah Alam, Malaysia. It discovers, enriches, and analyzes preschool competitors within a 10km radius of Bukit Jelutong.

## 📁 Required Files Structure

```
project_kestrel/
├── app.py                    # Streamlit dashboard (main interface)
├── enricher.py                # Data enrichment pipeline
├── fetcher.py                 # Data discovery engine  
├── requirements.txt            # Python dependencies
├── .env.example               # Environment variables template
├── README.md                 # This file
└── data/                     # Data directory
    ├── raw_discovered_centers.csv    # Input for enricher
    └── master.csv                # Final enriched output
```

## 🚀 Quick Start Guide

### Step 1: Environment Setup
```bash
# Clone repository
git clone <repository-url>
cd project_kestrel

# Create Python virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
```

### Step 2: Configure Environment
```bash
# Copy environment template
cp .env.example .env

# Edit .env with your API keys
TAVILY_KEY=your_tavily_api_key
TOMTOM_API_KEY=your_tomtom_api_key
```

### Step 3: Install Ollama (Required for AI features)
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama server
ollama serve

# Pull required model (new terminal)
ollama pull llama3.2:3b
```

### Step 4: Run Market Scan
```bash
# Option 1: Use Streamlit dashboard (recommended)
streamlit run app.py

# Option 2: Command line interface
python enricher.py --input data/raw_discovered_centers.csv --output data/master.csv
```

## 🎯 System Components

### 1. Data Discovery (fetcher.py)
- **Multi-source discovery**: Tavily API, Google Maps integration
- **Smart filtering**: ECE-specific keyword detection
- **Geographic targeting**: 10km radius from Bukit Jelutong
- **Output**: Raw discovered centers CSV

### 2. Data Enrichment (enricher.py)
- **Multi-service geocoding**: ArcGIS → TomTom → Ollama verification
- **AI-powered inference**: Ollama LLM for missing data
- **Deduplication**: Name similarity + address matching
- **Coordinate-to-address**: Converts GPS coordinates to readable addresses
- **ECE filtering**: Removes non-ECE centers before enrichment

### 3. Interactive Dashboard (app.py)
- **Threat scoring**: Competitive analysis rubric (0-10 scale)
- **Interactive maps**: Folium-based threat visualization
- **Memo builder**: AI-powered analysis generation
- **Data management**: Manual edit, import/export

## 📊 Key Features

### 🔍 Discovery Engine
- **Multi-lingual search**: English, Malay, Chinese queries
- **API integration**: Tavily, Google Maps, OpenStreetMap
- **Smart filtering**: ECE-specific keyword detection
- **Zone targeting**: Bukit Jelutong, Setia Alam, Elmina, etc.

### 🧠 Enrichment Pipeline
- **Dual geocoding**: ArcGIS + TomTom cross-verification
- **AI inference**: Ollama LLM for curriculum, fees, scale
- **Address resolution**: Coordinate → readable address conversion
- **Zero-blank policy**: Every field gets meaningful data

### 📈 Competitive Intelligence
- **Threat scoring**: 5-factor rubric (curriculum, orientation, scale, fees, proximity)
- **Brand targeting**: Brand A (Islamic, RM300-700) vs Brand B (Cambridge, RM1175)
- **Geospatial analysis**: 10km radius mapping with threat layers
- **AI memo generation**: Automated competitive analysis reports

## 🔧 Configuration

### Environment Variables (.env)
```bash
# Required API Keys
TAVILY_KEY=your_tavily_api_key_here
TOMTOM_API_KEY=your_tomtom_api_key_here

# Optional: Custom Target Location
DEFAULT_TARGET_LAT=3.1022
DEFAULT_TARGET_LNG=101.5333
DEFAULT_RADIUS_KM=10.0
```

### Dependencies (requirements.txt)
```
streamlit>=1.28.0
pandas>=2.0.0
folium>=0.14.0
streamlit-folium>=0.15.0
geopy>=2.4.0
requests>=2.31.0
python-dotenv>=1.0.0
tavily-python>=0.3.0
arcgis>=2.0.0
ollama-python>=0.3.0
```

## 🎯 Usage Examples

### Basic Market Scan
```bash
# Run complete pipeline
python fetcher.py --address "Persiaran Tebar Layar, Bukit Jelutong" --radius 10
python enricher.py
streamlit run app.py
```

### Custom Target Area
```bash
# Scan different location
python fetcher.py --address "Setia Alam, Shah Alam" --radius 8 --output data/setia_alam.csv
```

### Data Analysis
```bash
# Direct enrichment
python enricher.py --input custom_data.csv --output enriched_data.csv

# Test Ollama connection
python test_ollama.py
```

## 📊 Output Formats

### Raw Discovery (fetcher.py)
```csv
Brand,Address,Zone,Source,Confidence
Tadika ABC,"Jalan ABC 1, Shah Alam",Bukit Jelutong,Tavily,85
Taska XYZ,"Lot 123, Setia Alam",Setia Alam,Google Maps,92
```

### Enriched Data (enricher.py)
```csv
centre_name,official_name,address,neighbourhood,curriculum,religious_orientation,language_medium,scale,fee_fullday_raw,verification_note,enrichment_date
"Tadika Ceria","Tadika Ceria","No. 15, Jalan Bukit Jelutong, Shah Alam",Bukit Jelutong,"[Verified via Tavily] Montessori","[Verified via Tavily] Islamic-integrated","[Verified via Tavily] BM + English","[Verified via Tavily] Regional Chain","[Verified via Tavily] RM 450-650/month","ArcGIS: 92% | TomTom: 88% | Ollama: Valid Malaysian address","2024-04-02 13:00:00"
```

## 🗺️ Geographic Scope

### Primary Target Areas
- **Bukit Jelutong**: 3.1022°N, 101.5333°E (Target location)
- **Setia Alam**: 3.0679°N, 101.5175°E  
- **Elmina**: 3.1500°N, 101.4800°E
- **Denai Alam**: 3.1934°N, 101.5251°E
- **Glenmarie**: 3.0833°N, 101.5167°E

### Search Radius
- **Default**: 10km radius from target
- **Adjustable**: 1-15km via command line
- **Malaysia bounding box**: 1.0°N-7.5°N, 99.5°E-119.5°E

## 🤖 AI Integration

### Ollama LLM Features
- **Name correction**: Official business name verification
- **ECE classification**: Genuine vs non-ECE detection  
- **Address geocoding**: Coordinate → readable address conversion
- **Data inference**: Missing field completion
- **Memo generation**: Automated competitive analysis

### AI Model Configuration
- **Primary model**: llama3.2:3b (3B parameters)
- **Temperature**: 0.0-0.3 (deterministic responses)
- **Context window**: 128-256 tokens
- **Server**: http://127.0.0.1:11434

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

## 🔧 Troubleshooting

### Common Issues

#### Ollama Connection Failed
```bash
# Test Ollama connection
python test_ollama.py

# Restart Ollama
pkill ollama
ollama serve

# Pull model if missing
ollama pull llama3.2:3b
```

#### API Key Issues
```bash
# Verify environment variables
echo $TAVILY_KEY
echo $TOMTOM_API_KEY

# Test API connections
python -c "import tavily; print('Tavily OK' if tavily else 'Failed')"
python -c "import requests; print('TomTom OK' if requests.get('https://api.tomtom.com').status_code == 200 else 'Failed')"
```

#### Data Quality Issues
```bash
# Check input data format
head data/raw_discovered_centers.csv

# Run enrichment with debug
python enricher.py --input data/raw_discovered_centers.csv --output debug.csv

# Verify geocoding
python -c "import pandas as pd; df = pd.read_csv('data/master.csv'); print(df[['address', 'verification_note']].head())"
```

## 📄 License & Attribution

### Data Sources
- **Tavily API**: Web search and discovery
- **ArcGIS**: Geocoding and address validation
- **TomTom**: Secondary geocoding service
- **OpenStreetMap**: Geographic data and POI
- **Ollama**: Local AI inference

### Usage Rights
- **Commercial use**: Requires API key subscriptions
- **Research/educational**: Fair use under data analysis
- **Attribution required**: Cite data sources in reports

## 🤝 Contributing

### Development Setup
```bash
# Install development dependencies
pip install -r requirements.txt
pip install pytest black flake8

# Run tests
python -m pytest tests/

# Code formatting
black *.py
flake8 *.py
```

### Adding New Features
1. **Fork repository** and create feature branch
2. **Update documentation** in README.md
3. **Add tests** for new functionality
4. **Submit pull request** with detailed description

## 📞 Support

### Technical Issues
- **GitHub Issues**: Report bugs and feature requests
- **Documentation**: Check README.md and inline comments
- **Code comments**: Detailed function docstrings

### Business Inquiries
- **Project Kestrel**: Newmoon Capital internal project
- **Data licensing**: Commercial use requires proper API subscriptions

---

## 🎯 Quick Start Summary

```bash
# 1. Setup
git clone <repo>
cd project_kestrel
pip install -r requirements.txt
cp .env.example .env
# Edit .env with API keys

# 2. Start Ollama
ollama serve
ollama pull llama3.2:3b

# 3. Run
streamlit run app.py
# Open http://localhost:8501
```

**Dashboard will be available at http://localhost:8501**

The system will automatically:
1. Discover ECE centers in Shah Alam
2. Enrich data with multiple APIs and AI
3. Score competitive threats
4. Generate interactive maps and reports
5. Provide AI-powered competitive intelligence
