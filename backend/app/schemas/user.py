import uuid

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = None
    phone: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    lead_exophone: str | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str | None
    phone: str | None
    tier: str
    status: str
    lead_exophone: str | None

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
