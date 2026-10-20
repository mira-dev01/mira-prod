---
name: conventions
description: How code is written in MIRA — naming, structure, patterns, and style. Load when writing new code or reviewing existing code.
triggers:
  - "convention"
  - "pattern"
  - "naming"
  - "style"
  - "how should I"
  - "what's the right way"
  - "where does this go"
edges:
  - target: context/architecture.md
    condition: when a convention depends on understanding the system structure
  - target: context/stack.md
    condition: when a convention relates to a specific library or technology
  - target: patterns/add-endpoint.md
    condition: when adding a new API endpoint
  - target: patterns/add-model.md
    condition: when adding a new database model or migration
last_updated: 2026-08-08
---

# Conventions

## Naming

- **Python files**: snake_case (`pricing_engine.py`, `tool_handlers.py`, `calendar_service.py`)
- **API route files**: one file per domain under `app/api/v1/` (e.g. `properties.py`, `bookings.py`, `leads.py`)
- **SQLAlchemy model classes**: PascalCase (`Property`, `CallSession`, `GuestProfile`)
- **SQLAlchemy columns**: snake_case (`base_price`, `created_at`, `ical_url`)
- **Pydantic schemas**: `<Entity>Create` / `<Entity>Update` / `<Entity>Out` (e.g. `PropertyCreate`, `PropertyOut`)
- **Frontend route dirs**: kebab-case under `src/app/dashboard/` (e.g. `faq-gaps/`, `call-sessions/`)
- **Frontend components**: PascalCase files and exports (`TalkToMiraDialog`, `StatCard`)
- **Custom React hooks**: `use` prefix, camelCase (`useAsync`, `useDateRange`)

## Structure

- **Business logic lives in `app/services/`**, never in route handlers (`app/api/v1/`). Route handlers call service functions and format responses — nothing more.
- **Voice tool wrappers** (`app/voice/tools.py`) bind call-session context (`property_id`, `host_user_id`, `conversation_state`) via a factory closure `build_voice_tools()`. The actual handler logic is in `app/services/tool_handlers.py`. Never mix context-binding and business logic.
- **Guard processors** (`app/voice/*.py`) each own exactly one responsibility and are pass-through (zero latency) on normal turns. A new recurring LLM compliance failure → new guard processor, not just a prompt update.
- **Config**: all env vars are documented and validated in `app/config.py`'s `Settings` class. Never read `os.environ` directly anywhere else.
- **Tests**: all async; all hit a real test DB; no mocking of the DB layer. Test files live in `backend/tests/`.
- **Frontend API calls**: always go through `frontend/src/lib/api.ts`; never raw `fetch()`. The `api` object has typed domain groups (`api.properties`, `api.bookings`, etc.).

## Patterns

**1. FastAPI protected route (the standard pattern)**
```python
# Correct
from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user

@router.get("/{property_id}")
async def get_property(
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PropertyOut:
    prop = await get_owned_property(property_id, current_user.id, db)
    return PropertyOut.model_validate(prop)

# Wrong — logic in handler, auth missing, raw DB query
@router.get("/{property_id}")
async def get_property(property_id: uuid.UUID):
    result = await db.execute(select(Property).where(...))
    ...
```

**2. New SQLAlchemy model (always use both mixins)**
```python
# Correct
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.database import Base

class MyEntity(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "my_entities"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))

# Wrong — manual id/timestamps
class MyEntity(Base):
    __tablename__ = "my_entities"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

**3. Tool handler return value (always natural-language string)**
```python
# Correct — TTS-ready string
async def handle_check_calendar(property_id, checkin, checkout, ...) -> str:
    available = await calendar_service.is_available(...)
    if available:
        return f"Great news! The property is available from {checkin} to {checkout}."
    return f"The property is already booked for those dates."

# Wrong — structured dict (never returned from a tool handler)
async def handle_check_calendar(...) -> dict:
    return {"available": True, "dates": [...]}
```

**4. APScheduler job (always in `main.py` lifespan)**
```python
# In lifespan(), before scheduler.start():
scheduler.add_job(_my_job, "interval", minutes=30, id="my_job_id")
# For immediate startup run:
asyncio.create_task(_my_job())
```

## Verify Checklist

Before presenting any code change:
- [ ] Business logic is in `app/services/`, not in `app/api/v1/` route handlers
- [ ] Protected routes use `Depends(get_db)` + `Depends(get_current_user)`
- [ ] New DB model inherits `UUIDPkMixin` + `TimestampMixin` from `app/models/mixins.py`
- [ ] New migration created with `alembic revision --autogenerate -m "..."` and reviewed (not blindly applied)
- [ ] Tool handlers return a natural-language `str`, not a dict or structured type
- [ ] No `os.environ` reads outside `app/config.py`
- [ ] New scheduled job added in `lifespan()` in `app/main.py`, not started ad-hoc elsewhere
- [ ] Frontend API calls go through `frontend/src/lib/api.ts`, not raw `fetch()`
- [ ] Guard processors are pass-through on normal turns; activate only on the specific condition they exist for
