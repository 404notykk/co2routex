# CO2RouteX — Advanced Multi-Modal CO₂ Transport Cost Model for CCS Projects


Open-source framework for realistic cost modeling of multi-modal CO₂ transport (pipeline, ship, rail, truck) in CCS projects. Includes geotechnical & societal factor processing, node mapping with intelligent connection discovery, mode-specific cost functions, and optimal routing with realistic pipeline arc/bend calculations.

---

## ✨ Features

- **Geotechnical & Societal Factor Processing:** Data extraction, clipping, aggregation, spatial consistency & standardization
- **Node & Network Builder:** Automatic mapping of emission sources, sinks, utilizers and transport switches; intelligent connection discovery
- **Multi-Modal Cost Engine:** Mode-specific cost functions (pipeline, ship, rail, truck)
- **Realistic Routing:** Optimal pathfinding with actual arc/bend pipeline length calculation


## 🚀 Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/404notykk/co2routetex.git
cd co2routetex

# 2. Create and activate virtual environment (recommended)
python -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run example
python src/main.py --config config/default.yaml
