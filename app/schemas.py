import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, model_validator


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
    created_at: datetime

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
    vet_certificate_url: str | None = None
    image_url: str | None = None  # Deprecated: use image_urls instead
    image_urls: list[str] | None = None  # List of image URLs

    @model_validator(mode='after')
    def validate_vet_certificate(self):
        if self.vet_check_available and not self.vet_certificate_url:
            raise ValueError('vet_certificate_url is required when vet_check_available is True')
        return self


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
    vet_certificate_url: str | None = None
    image_url: str | None = None  # Deprecated: use image_urls instead
    image_urls: list[str] | None = None  # Replace all images with this list

    @model_validator(mode='after')
    def validate_vet_certificate(self):
        if self.vet_check_available and not self.vet_certificate_url:
            raise ValueError('vet_certificate_url is required when vet_check_available is True')
        return self



# ── Horse Responses ───────────────────────────────────────────────────────────


class HorseImageResponse(BaseModel):
    id: uuid.UUID
    image_url: str
    display_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class HorseOwnerResponse(BaseModel):
    id: uuid.UUID
    email: str
    is_verified: bool
    phone_number: str | None = None

    model_config = {"from_attributes": True}

    @model_validator(mode='before')
    @classmethod
    def extract_phone_from_profile(cls, data):
        # If data is a SQLAlchemy model object
        if hasattr(data, 'profile'):
            profile = getattr(data, 'profile', None)
            if profile and hasattr(profile, 'phone_number'):
                # Create a dict with all needed fields
                return {
                    'id': data.id,
                    'email': data.email,
                    'is_verified': data.is_verified,
                    'phone_number': profile.phone_number
                }
        return data


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
    vet_certificate_url: str | None
    image_url: str | None  # Deprecated: use images instead
    created_at: datetime
    updated_at: datetime
    owner: HorseOwnerResponse | None = None
    images: list[HorseImageResponse] = []  # List of all images

    model_config = {"from_attributes": True}


class HorseListResponse(BaseModel):
    total: int
    horses: list[HorseResponse]
