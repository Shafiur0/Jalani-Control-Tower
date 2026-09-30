---
name: rebuild-services
description: Rebuilds one or more Jalani Control Tower Docker services (backend-api, frontend, intelligence-service).
---

# Rebuilding Jalani Services

When asked to rebuild a service, follow these exact steps to ensure the containers are rebuilt and started correctly without orphan containers.

## Steps

1. **Identify the target service**:
   The services are: `backend-api`, `frontend`, `intelligence-service`, `simulator-api`.

2. **Execute the Rebuild Command**:
   Use `docker compose up --build -d <service_name>` to rebuild the specific container in the background.
   - Example: `docker compose up --build -d backend-api`

3. **Verify the Rebuild**:
   - Wait for the build to finish.
   - Run `docker compose logs --tail 20 <service_name>` to ensure it started successfully and is not crash-looping.
   - If it's the `backend-api`, explicitly check for any `ValidationError` in the logs to ensure your code changes didn't break Pydantic schemas.

4. **Notify the User**:
   - For the frontend: Remind the user to perform a **Hard Refresh** (Ctrl+F5) to clear their browser cache.
   - For the backend: Confirm that the API is now pulling data cleanly.
