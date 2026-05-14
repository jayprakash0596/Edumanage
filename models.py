from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Literal
from datetime import datetime

class AdmissionCreate(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    phone: str
    course: str
    dob: str
    address: str
    semester: int = 1
    totalSemesters: int = 6
    status: str = "Pending"
    createdAt: datetime = Field(default_factory=datetime.utcnow)

class PaymentCreate(BaseModel):
    amount: int
    currency: str = "INR"
    receipt: str
    notes: Optional[dict] = None

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class ChatMessage(BaseModel):
    message: str
    history: List[ChatTurn] = Field(default_factory=list)

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str
    name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str
    role: str

class ScheduleCreate(BaseModel):
    time: str
    course: str
    room: str
    type: str
    staff_email: EmailStr

class SubjectCreate(BaseModel):
    code: str
    name: str
    progress: int
    color: str
    student_email: EmailStr

class NotificationCreate(BaseModel):
    type: str
    message: str
    student_email: EmailStr
    is_read: bool = False
    createdAt: datetime = Field(default_factory=datetime.utcnow)
