import uuid
from datetime import datetime
import enum
from pydantic import BaseModel, EmailStr, Field, model_validator


# ── Auth Requests ─────────────────────────────────────────────────────────────


def normalize_numerals(v: str | float | int | None) -> str | float | int | None:
    if isinstance(v, str):
        # Dictionary mapping Eastern Arabic numerals to Western Arabic numerals
        arabic_to_western = {
            '٠': '0', '١': '1', '٢': '2', '٣': '3', '۴': '4',
            '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
            '۰': '0', '۱': '1', '۲': '2', '３': '3', '٤': '4',
            '۵': '5', '۶': '6', '７': '7', '８': '8', '９': '9'
        }
        for arabic, western in arabic_to_western.items():
            v = v.replace(arabic, western)
    return v


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: str = "buyer"
    phone_number: str | None = Field(None, max_length=20)
    location: str | None = Field(None, max_length=255)
    language: str = "en"  # "en" or "ar"

    @model_validator(mode='before')
    @classmethod
    def convert_numerals(cls, data):
        if isinstance(data, dict):
            if 'phone_number' in data and data['phone_number']:
                data['phone_number'] = normalize_numerals(data['phone_number'])
        return data


class OTPRequest(BaseModel):
    email: EmailStr


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)

    @model_validator(mode='before')
    @classmethod
    def convert_numerals(cls, data):
        if isinstance(data, dict):
            if 'otp' in data and data['otp']:
                data['otp'] = normalize_numerals(data['otp'])
        return data


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Auth Responses ────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfileResponse(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None
    location: str | None

    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    is_verified: bool
    language: str
    profile: UserProfileResponse | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    phone_number: str | None = Field(None, max_length=20)
    location: str | None = Field(None, max_length=255)
    role: str | None = None  # Allow updating role
    language: str | None = None

    @model_validator(mode='before')
    @classmethod
    def convert_numerals(cls, data):
        if isinstance(data, dict):
            if 'phone_number' in data and data['phone_number']:
                data['phone_number'] = normalize_numerals(data['phone_number'])
        return data


class UserRoleUpdate(BaseModel):
    role: str


class AdminUserUpdate(BaseModel):
    role: str | None = None
    is_verified: bool | None = None
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
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
    
    # Discount fields
    discount_type: str | None = Field(None, pattern="^(percentage|fixed)$")
    discount_value: float | None = Field(None, gt=0)

    @model_validator(mode='after')
    def validate_vet_certificate(self):
        if self.vet_check_available and not self.vet_certificate_url:
            raise ValueError('vet_certificate_url is required when vet_check_available is True')
        return self

    @model_validator(mode='before')
    @classmethod
    def convert_numerals(cls, data):
        if isinstance(data, dict):
            # Normalize numeric fields
            fields_to_normalize = ['price', 'age', 'height', 'discount_value']
            for field in fields_to_normalize:
                if field in data and data[field] is not None:
                    # Only normalize strings
                    if isinstance(data[field], str):
                         data[field] = normalize_numerals(data[field])
        return data


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
    
    # Discount fields
    discount_type: str | None = Field(None, pattern="^(percentage|fixed)$")
    discount_value: float | None = Field(None, gt=0)
    # Allow clearing discount by passing specific value or handling None differently? 
    # For now, let's assume if they pass discount_type='percentage' they must pass value.
    # To clear, they might need a specific way, or we just allow setting them to Null in the model if we pass explicit None here?
    # Pydantic defaults to None means "do not update" usually in PATCH. 
    # We might need a way to unset it. But for now let's just add the fields.

    @model_validator(mode='after')
    def validate_vet_certificate(self):
        if self.vet_check_available and not self.vet_certificate_url:
            raise ValueError('vet_certificate_url is required when vet_check_available is True')
        return self

    @model_validator(mode='before')
    @classmethod
    def convert_numerals(cls, data):
        if isinstance(data, dict):
            # Normalize numeric fields
            fields_to_normalize = ['price', 'age', 'height', 'discount_value']
            for field in fields_to_normalize:
                if field in data and data[field] is not None:
                    # Only normalize strings
                    if isinstance(data[field], str):
                         data[field] = normalize_numerals(data[field])
        return data



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
    
    # Discount fields
    discount_type: str | None = None
    discount_value: float | None = None
    discount_price: float | None = None
    status: str = "approved"  # pending_review, approved, rejected
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    owner: HorseOwnerResponse | None = None
    images: list[HorseImageResponse] = []  # List of all images

    model_config = {"from_attributes": True}


class HorseListResponse(BaseModel):
    total: int
    horses: list[HorseResponse]

# ── Favorite Responses ────────────────────────────────────────────────────────

class FavoriteResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    horse_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class AddFavoriteRequest(BaseModel):
    horse_id: uuid.UUID


# -- Admin Review Requests ----

class AdminApproveListingRequest(BaseModel):
    pass  # No additional fields needed for approval


class AdminRejectListingRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# ── Voucher Schemas ───────────────────────────────────────────────────────────

class VoucherCreateRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    discount_type: str = Field(..., pattern="^(percentage|fixed)$")
    discount_value: float = Field(..., gt=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    usage_limit: int | None = Field(None, gt=0)
    is_active: bool = True

class VoucherUpdateRequest(BaseModel):
    discount_type: str | None = Field(None, pattern="^(percentage|fixed)$")
    discount_value: float | None = Field(None, gt=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    usage_limit: int | None = Field(None, gt=0)
    is_active: bool | None = None

class VoucherResponse(BaseModel):
    id: uuid.UUID
    code: str
    discount_type: str
    discount_value: float
    valid_from: datetime | None
    valid_until: datetime | None
    usage_limit: int | None
    used_count: int
    is_active: bool
    created_at: datetime
    
    model_config = {"from_attributes": True}

class VoucherValidateRequest(BaseModel):
    code: str
    horse_id: uuid.UUID | None = None # Optional context to check if it applies (if we had specific vouchers)
    current_price: float | None = None # To calculate discount on the fly if needed

class VoucherValidateResponse(BaseModel):
    valid: bool
    message: str
    discount_type: str | None = None
    discount_value: float | None = None
    new_price: float | None = None # Calculated hypothetical price