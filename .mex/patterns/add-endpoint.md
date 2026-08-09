---
name: add-endpoint
description: Add a new REST API endpoint to the FastAPI backend. Covers route handler, service function, schema, and auth wiring.
triggers:
  - "add endpoint"
  - "new route"
  - "new API"
  - "add route"
  - "REST endpoint"
edges:
  - target: context/conventions.md
    condition: for naming, structure, and the verify checklist
  - target: context/architecture.md
    condition: for understanding which domain router to add to
  - target: patterns/add-model.md
    condition: if the endpoint also needs a new database model or migration
last_updated: 2026-08-08
---

# Add Endpoint

## Context

Load `context/conventions.md` before starting. Every endpoint in this project follows a strict layering:
- Route handler → `app/api/v1/<domain>.py`: validate inputs, call a service, return a schema
- Business logic → `app/services/<domain>_service.py`: DB queries, state changes, external calls
- Schema → `app/schemas/<domain>.py`: Pydantic `<Entity>Create`, `<Entity>Update`, `<Entity>Out`

Auth is handled by `Depends(get_current_user)` from `app/auth/dependencies.py`. Every protected route must use it.

## Steps

1. **Identify or create the schema** in `app/schemas/<domain>.py`:
   - Request body: `class MyEntityCreate(BaseModel):`
   - Response: `class MyEntityOut(BaseModel): model_config = ConfigDict(from_attributes=True)`

2. **Add the service function** in `app/services/<domain>_service.py`:
   - Accept `db: AsyncSession` as first arg
   - Use `await db.execute(select(...))` pattern with `AsyncSession`
   - For writes: `db.add(entity)` then `await db.commit()` then `await db.refresh(entity)`

3. **Add the route handler** in `app/api/v1/<domain>.py`:
   ```python
   @router.post("/my-entities", status_code=status.HTTP_201_CREATED)
   async def create_my_entity(
       body: MyEntityCreate,
       db: AsyncSession = Depends(get_db),
       current_user: User = Depends(get_current_user),
   ) -> MyEntityOut:
       entity = await my_service.create_entity(db, current_user.id, body)
       return MyEntityOut.model_validate(entity)
   ```

4. **Verify the router is mounted** in `app/main.py` under `API_PREFIX = "/api/v1"`. If adding to an existing domain router (e.g. `properties.router`), it's already mounted — no change needed. If adding a new domain file, add `from app.api.v1 import my_domain` and `app.include_router(my_domain.router, prefix=API_PREFIX)`.

5. **Add the frontend API call** in `frontend/src/lib/api.ts` under the relevant domain group:
   ```typescript
   myEntities: {
     create: (data: MyEntityCreate) =>
       request<MyEntityOut>("/my-entities", { method: "POST", body: JSON.stringify(data) }),
   }
   ```

6. **Kill and restart uvicorn** after changes — `--reload` picks up `.py` changes but a full restart is needed if you changed route structure. Verify the new route exists: `curl localhost:8000/openapi.json | jq '.paths | keys'`.

## Gotchas

- **`get_owned_property` for ownership checks**: use `app/api/v1/common.get_owned_property(property_id, current_user.id, db)` for any route that fetches a property by ID — it raises `404` if not found and `403` if not owned by the current user. Never do a raw `select(Property).where(Property.id == property_id)` in a route handler without ownership check.
- **`405 Method Not Allowed` instead of `404`**: if you see this on your new route, you have a stale uvicorn process. Kill completely and restart.
- **Async DB pattern**: always `await db.execute(...)`, never the sync SQLAlchemy pattern. The engine is `create_async_engine` — mixing sync and async will deadlock.
- **`expire_on_commit=False`** is set on `AsyncSessionLocal` — refreshed objects are safe to return after commit without a re-fetch, but relationships not eagerly loaded will still be empty.
- **Route ordering matters in FastAPI**: if you add `GET /properties/export` and there's already a `GET /properties/{property_id}`, `export` must come BEFORE the path param route or FastAPI will try to parse "export" as a UUID.

## Verify

- [ ] Business logic is in `app/services/`, not in the route handler
- [ ] Route uses `Depends(get_current_user)` for auth (unless deliberately public)
- [ ] Response schema uses `ConfigDict(from_attributes=True)` and `model_validate()`
- [ ] No raw `os.environ` reads
- [ ] New route is in the correct domain router (already mounted, or added to `main.py`)
- [ ] `curl localhost:8000/openapi.json` confirms the route exists before testing
- [ ] Frontend call added to `api.ts` domain group (if frontend will consume it)

## Debug

- Route returns `404`: check the router's `prefix` in `app/api/v1/<domain>.py` + `API_PREFIX` in `main.py`. Full path = `API_PREFIX` + router prefix + route path.
- Route returns `405`: stale process — kill and restart uvicorn fully.
- `422 Unprocessable Entity`: Pydantic validation failure on the request body — check the schema and the test request body match.
- Auth `401`: token expired or `mira_token` not in localStorage — re-login.

## Update Scaffold

- [ ] Update `.mex/ROUTER.md` "Current Project State" if a new domain was added
- [ ] If this was a new domain router, add it to `context/architecture.md`'s component list
- [ ] If this task type has unique gotchas not covered here, update this pattern
