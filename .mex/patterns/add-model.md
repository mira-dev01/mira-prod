---
name: add-model
description: Add a new SQLAlchemy model and Alembic migration. Covers model class, schema, migration generation, and deployment.
triggers:
  - "add model"
  - "new table"
  - "database model"
  - "alembic migration"
  - "new migration"
  - "schema change"
edges:
  - target: context/conventions.md
    condition: for model naming, mixin usage, and the verify checklist
  - target: context/stack.md
    condition: for SQLAlchemy async patterns and version constraints
  - target: patterns/add-endpoint.md
    condition: if the new model also needs a REST endpoint
last_updated: 2026-08-08
---

# Add Model

## Context

Load `context/conventions.md` before starting. All models live in `backend/app/models/`. Every model:
- Inherits `UUIDPkMixin` (UUID `id` PK, `default=uuid.uuid4`) + `TimestampMixin` (`created_at`/`updated_at`, timezone-aware, server-defaulted) from `app/models/mixins.py`
- Inherits `Base` (the `DeclarativeBase` in `app/database.py`)
- Is exported from `app/models/__init__.py` so Alembic can see it

Current DB head: `6aa03c77c36f` (add airbnb_latitude/longitude to properties). Run `alembic heads` to verify your local state matches.

## Steps

1. **Create the model file** `app/models/my_entity.py`:
   ```python
   import uuid
   from sqlalchemy import ForeignKey, String, Text
   from sqlalchemy.orm import Mapped, mapped_column, relationship
   from app.database import Base
   from app.models.mixins import TimestampMixin, UUIDPkMixin

   class MyEntity(UUIDPkMixin, TimestampMixin, Base):
       __tablename__ = "my_entities"

       user_id: Mapped[uuid.UUID] = mapped_column(
           ForeignKey("users.id", ondelete="CASCADE"), nullable=False
       )
       name: Mapped[str] = mapped_column(String(255), nullable=False)
       description: Mapped[str | None] = mapped_column(Text, nullable=True)

       # Relationship back to User
       user: Mapped["User"] = relationship(back_populates="my_entities")
   ```

2. **Add the back-reference** to `app/models/user.py` (or whichever parent model):
   ```python
   my_entities: Mapped[list["MyEntity"]] = relationship(
       back_populates="user", cascade="all, delete-orphan"
   )
   ```

3. **Export from `app/models/__init__.py`**:
   ```python
   from app.models.my_entity import MyEntity  # noqa: F401
   ```
   Alembic's `env.py` imports `Base` from `app.database` and reads all metadata — the model must be importable from `__init__.py` for `autogenerate` to see it.

4. **Generate the migration**:
   ```bash
   cd backend
   alembic revision --autogenerate -m "add my_entities table"
   ```
   **Review the generated file** in `alembic/versions/` before applying. Confirm:
   - `create_table("my_entities", ...)` is present
   - Columns match the model exactly
   - Foreign key has `ondelete="CASCADE"` if you set it on the model
   - The `downgrade()` function drops the table

5. **Apply the migration**:
   ```bash
   alembic upgrade head
   ```

6. **Create schemas** in `app/schemas/my_entity.py`:
   ```python
   from pydantic import BaseModel, ConfigDict
   import uuid

   class MyEntityCreate(BaseModel):
       name: str
       description: str | None = None

   class MyEntityOut(BaseModel):
       model_config = ConfigDict(from_attributes=True)
       id: uuid.UUID
       name: str
       description: str | None
       user_id: uuid.UUID
   ```

7. **Update `docs/database.md`** with the new table entry (name, column list, notes).

## Gotchas

- **`autogenerate` won't see the model** unless it's imported before Alembic runs. The `app/models/__init__.py` import is the mechanism — don't skip it.
- **`sslmode`/`ssl` in `DATABASE_URL`** is stripped by `config.py` before Alembic sees it. If running `alembic` directly with a URL that has `?sslmode=require`, the validator won't run — use the URL format from `.env` (without `sslmode`; set `database_requires_ssl=true` instead).
- **Never use `Integer` PKs** — all tables use UUID PKs via `UUIDPkMixin`. Alembic will generate `Integer` if you forget `UUIDPkMixin`.
- **`cascade="all, delete-orphan"`** on the parent relationship is required if you want child rows deleted when the parent is deleted. The `ondelete="CASCADE"` on the FK is the DB-level constraint; the SQLAlchemy `cascade` is the ORM-level equivalent — both are needed.
- **JSONB columns**: use `from sqlalchemy import JSON` with `mapped_column(JSON, default=list)` for JSONB arrays. Alembic renders this as `postgresql.JSON` — check the generated file uses the right dialect.
- **Reviewing migrations before apply**: `autogenerate` sometimes picks up unintended changes (column type coercions, index renames). Always read the file before `alembic upgrade head`.

## Verify

- [ ] Model inherits `UUIDPkMixin`, `TimestampMixin`, `Base` (in that order)
- [ ] Model exported from `app/models/__init__.py`
- [ ] Back-reference added to parent model with `cascade="all, delete-orphan"`
- [ ] Migration file reviewed — `create_table` present, `downgrade` drops the table
- [ ] `alembic upgrade head` applied successfully on local DB
- [ ] `docs/database.md` updated with new table entry

## Debug

- **`autogenerate` produces empty migration**: model not imported. Check `app/models/__init__.py`.
- **`alembic upgrade head` fails**: check `alembic heads` — the DB may be on a different revision. If ahead, `alembic downgrade -1` to roll back.
- **`missing column` error at runtime after deploy**: migration was applied locally but not on the production DB. Run `alembic upgrade head` on the production DB, or confirm the Dockerfile `CMD` ran it on deploy.
- **FK constraint error on insert**: parent row doesn't exist, or the FK column name doesn't match the parent table's PK column. Check `ForeignKey("users.id", ...)` matches `users.id`.

## Update Scaffold

- [ ] Update `docs/database.md` with the new table (name, columns, notes)
- [ ] Update `.mex/ROUTER.md` "Current Project State" if this is a significant new capability
- [ ] If the migration changes the current head revision, update `context/setup.md` "Common Issues" DB section
