import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ── Auth Requests ─────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: str = "buyer"  # defaulting to string to avoid circular imports if UserRole isn't available here, but better to use Enum if possible. Let's check imports.
    phone_number: str | None = Field(None, max_length=20)
    location: str | None = Field(None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Auth Responses ────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfileResponse(BaseModel):
    phone_number: str | None
    location: str | None

    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    is_verified: bool
    profile: UserProfileResponse | None = None

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    phone_number: str | None = Field(None, max_length=20)
    location: str | None = Field(None, max_length=255)
    role: str | None = None  # Allow updating role


class UserRoleUpdate(BaseModel):
    role: str


class AdminUserUpdate(BaseModel):
    role: str | None = None
    is_verified: bool | None = None
    phone_number: str | None = Field(None, max_length=20)
    location: str | None = Field(None, max_length=255)


# ── Horse Requests ────────────────────────────────────────────────────────────


class HorseCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    price: float = Field(..., gt=0)
    breed: str = Field(..., min_length=1, max_length=100)
    age: int = Field(..., ge=0)
    gender: str = Field(..., pattern="^(mare|gelding|stallion)$")
    discipline: str | None = Field(None, max_length=100)
    height: float | None = Field(None, gt=0)
    description: str | None = None
    vet_check_available: bool = False
    image_url: str | None = None


class HorseUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    price: float | None = Field(None, gt=0)
    breed: str | None = Field(None, min_length=1, max_length=100)
    age: int | None = Field(None, ge=0)
    gender: str | None = Field(None, pattern="^(mare|gelding|stallion)$")
    discipline: str | None = Field(None, max_length=100)
    height: float | None = Field(None, gt=0)
    description: str | None = None
    vet_check_available: bool | None = None
    image_url: str | None = None


# ── Horse Responses ───────────────────────────────────────────────────────────

class HorseOwnerResponse(BaseModel):
    id: uuid.UUID
    email: str

    model_config = {"from_attributes": True}


class HorseResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    price: float
    breed: str
    age: int
    gender: str
    discipline: str | None
    height: float | None
    description: str | None
    vet_check_available: bool
    image_url: str | None
    created_at: datetime
    updated_at: datetime
    owner: HorseOwnerResponse | None = None

    model_config = {"from_attributes": True}


class HorseListResponse(BaseModel):
    total: int
    horses: list[HorseResponse]
