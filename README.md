<div align="center">
  <h1>🛢️ Jalani Control Tower</h1>
  <p><strong>Intelligent Fuel Supply Chain Orchestrator</strong></p>

  [![Build Status](https://github.com/Shafiur0/Jalani-Control-Tower/actions/workflows/docker-compose-test.yml/badge.svg)](https://github.com/Shafiur0/Jalani-Control-Tower/actions/workflows/docker-compose-test.yml)
  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
  [![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-0.103.1-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com/)
  [![Docker](https://img.shields.io/badge/Docker-Supported-2496ED.svg?logo=docker)](https://www.docker.com/)
</div>

---

Jalani Control Tower is a comprehensive automated fuel supply chain management and decision-making system. It was built as a final project for a BUP hackathon.

The system integrates with a fuel supply simulator and uses intelligent algorithms to monitor depot inventories, forecast station demand, and autonomously generate optimal dispatch allocations to prevent fuel stockouts.

## 🏗️ Architecture

The project consists of three main components:

1. 🎮 **Simulator API** (`asifmahmoud414/bup-fuel-supply-simulator`)
   - Simulates the environment: depots, fuel stations, routes, and demand over time.
   - Provides endpoints to inspect inventory and dispatch fuel trucks.

2. 🧠 **Intelligence Service** (`intelligence-service/`)
   - Built with FastAPI, NumPy, and SciPy.
   - **Demand Forecasting**: Uses Exponentially Weighted Moving Average (EWMA) and seasonal patterns to predict fuel demand per station.
   - **LP Optimizer**: Uses a Linear Programming (LP) solver (`highs`) to determine optimal fuel allocations from depots to stations, minimizing stockout risk while respecting depot dispatch limits, route capacities, and station headrooms.

3. ⚙️ **Backend API** (`backend-api/`)
   - Built with FastAPI.
   - Acts as the central state manager and orchestrator.
   - Polls the simulator for real-time state updates (stations, depots, allocations).
   - Forwards the state to the intelligence service.
   - Executes approved decisions by sending them to the simulator.

4. 🖥️ **Frontend Dashboard** (`frontend/`)
   - Built with React, Vite, and Tailwind CSS (or similar).
   - Visualizes real-time metrics, station health, and active deliveries.

---

## 🚀 Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Shafiur0/Jalani-Control-Tower.git
   cd Jalani-Control-Tower
   ```

2. **Start the system:**
   Run the following command to build and start all microservices:
   ```bash
   docker compose up --build -d
   ```
   This will spin up the `simulator-api`, `intelligence-service`, `backend-api`, and `frontend`.

### 📊 Monitoring the Services

Once the containers are up and running, you can access the services at:

- **Frontend Dashboard**: `http://localhost:3000` (if configured)
- **Backend API Docs**: [http://localhost:8080/docs](http://localhost:8080/docs)
- **Intelligence Service Docs**: [http://localhost:8081/docs](http://localhost:8081/docs)
- **Simulator Status**: `http://localhost:8000/v1/instance`

### 🛠️ Simulation Controls

You can manually control the simulation via the simulator admin endpoints. For example, to start the simulation:
```bash
curl -X POST http://localhost:8000/admin/run
```

---

## 🔄 Workflow

1. ⏱️ The simulator runs and simulates fuel consumption across various fuel stations.
2. 📡 The `backend-api` continuously polls the state.
3. 🧠 The state is sent to `intelligence-service` which generates optimal fuel dispatch decisions.
4. ✅ If a decision is approved (manually or via autopilot), `backend-api` automatically sends a dispatch command to the simulator.
5. 🚚 The fuel trucks depart from the depots, and after a set travel time, arrive at the destination stations, restocking the fuel.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
