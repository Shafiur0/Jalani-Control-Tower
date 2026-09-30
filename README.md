# Jalani Control Tower

Jalani Control Tower is a comprehensive automated fuel supply chain management and decision-making system. It was built as a final project for a BUP hackathon.

The system integrates with a fuel supply simulator and uses intelligent algorithms to monitor depot inventories, forecast station demand, and autonomously generate optimal dispatch allocations to prevent fuel stockouts.

## Architecture

The project consists of three main components:

1. **Simulator API** (`asifmahmoud414/bup-fuel-supply-simulator`)
   - Simulates the environment: depots, fuel stations, routes, and demand over time.
   - Provides endpoints to inspect inventory and dispatch fuel trucks.

2. **Backend API** (`backend-api/`)
   - Built with FastAPI.
   - Acts as the central state manager and orchestrator.
   - Polls the simulator for real-time state updates (stations, depots, allocations).
   - Forwards the state to the intelligence service.
   - Executes approved decisions by sending them to the simulator.

3. **Intelligence Service** (`intelligence-service/`)
   - Built with FastAPI, NumPy, and SciPy.
   - **Demand Forecasting**: Uses Exponentially Weighted Moving Average (EWMA) and seasonal patterns to predict fuel demand per station.
   - **LP Optimizer**: Uses a Linear Programming (LP) solver (`highs`) to determine optimal fuel allocations from depots to stations, minimizing stockout risk while respecting depot dispatch limits, route capacities, and station headrooms.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Getting Started

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Shafiur0/Jalani-Control-Tower.git
   cd Jalani-Control-Tower
   ```

2. **Start the system:**
   Run the following command to build and start the microservices:
   ```bash
   docker compose up --build -d
   ```
   This will spin up the `simulator-api`, `intelligence-service`, and `backend-api`.

3. **Monitor the Simulation:**
   - Simulator: `http://localhost:8000`
   - Backend API: `http://localhost:8080/api/state`
   - Intelligence Service: `http://localhost:8081`

4. **Simulation Controls:**
   You can manually control the simulation via the simulator endpoints (e.g., POST to `http://localhost:8000/admin/run` to start the simulation).

## Workflow

1. The simulator runs and simulates fuel consumption across various fuel stations.
2. The `backend-api` continuously polls the state.
3. The state is sent to `intelligence-service` which generates optimal fuel dispatch decisions.
4. If a decision is approved, `backend-api` automatically sends a dispatch command to the simulator.
5. The fuel trucks depart from the depots, and after a set travel time, arrive at the destination stations, restocking the fuel.

## License
MIT License
