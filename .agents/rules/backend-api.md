---
trigger: model_decision
description: "Guidelines and gotchas for the Jalani Control Tower backend API, especially when parsing data from the Simulator."
---

# Jalani Control Tower Backend Rules

When modifying the backend API (Python/FastAPI) or integrating with the Simulator API, you MUST follow these guidelines:

## Simulator API Casing Gotchas
1. **Fuel Types**: The Simulator API ALWAYS returns fuel keys in ALL CAPS (e.g., `"DIESEL"`, `"PETROL"`, `"OCTANE"`). The internal Pydantic models expect lowercase. **Always safely check for uppercase keys when parsing `.get("DIESEL", .get("diesel", 0))`**.
2. **Statuses**: The Simulator API might return statuses in uppercase (e.g., `"AVAILABLE"`). Always `.lower()` strings before validating them against Pydantic Enum fields.
3. **IDs**: The Simulator API sometimes returns IDs (like event IDs) as Integers (e.g., `id: 1`). Internal models expect Strings. Always coerce IDs to strings using `str(data.get("id"))`.

## Dispatch Rate
- When fetching depots from the Simulator API, the field for dispatch rate is named `"dispatch_capacity_per_tick"`. It is NOT named `dispatch_rate` or `max_dispatch_per_tick`.

## General Code Style
- Always use asynchronous HTTP clients (`httpx.AsyncClient`) for internal microservice communication.
- Log important optimization and processing metrics using the `jalani.intelligence` or `jalani.main` loggers.
