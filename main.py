import os
import razorpay
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from database import get_db
from models import AdmissionCreate, PaymentCreate, ChatMessage, UserCreate, UserLogin, ScheduleCreate, SubjectCreate, NotificationCreate
from auth import get_password_hash, verify_password, create_access_token, verify_token, SECRET_KEY, ALGORITHM
from groq import AsyncGroq
from dotenv import load_dotenv
import jwt

load_dotenv()

app = FastAPI(title="EduManage API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Razorpay Client
razorpay_client = razorpay.Client(
    auth=(os.getenv("RAZORPAY_KEY_ID", "dummy_key"), 
          os.getenv("RAZORPAY_KEY_SECRET", "dummy_secret"))
)

# Initialize Groq Client
groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
groq_client = AsyncGroq(api_key=groq_api_key) if groq_api_key else None

def _parse_currency_amount(amount_text: str) -> int:
    digits = "".join(ch for ch in str(amount_text) if ch.isdigit())
    return int(digits) if digits else 0

def _safe_percentage(subjects):
    if not subjects:
        return "N/A"
    total = sum(subject.get("progress", 0) for subject in subjects)
    return f"{int(total / len(subjects))}%"

def _format_notification_list(notifications):
    if not notifications:
        return "No notifications"
    return "; ".join(
        f"{item.get('type', 'INFO')}: {item.get('message', '')}"
        for item in notifications[:5]
    )

async def _get_token_payload_from_request(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None

async def _build_user_context(db, token_payload: dict | None):
    if not token_payload:
        return {
            "role": "guest",
            "email": None,
            "facts": ["User is not logged in."],
            "direct_answer": {},
        }

    role = token_payload.get("role", "guest")
    email = token_payload.get("sub")
    facts = [f"Current role: {role}", f"Current email: {email}"]
    direct_answer = {}

    if role == "Student":
        admission = await db.admissions.find_one({"email": email})
        approved_admission = await db.admissions.find_one({"email": email, "status": "Approved"})
        subjects = []
        notifications = []

        async for doc in db.subjects.find({"student_email": email}):
            subjects.append(doc)
        async for doc in db.notifications.find({"student_email": email}).limit(5):
            notifications.append(doc)

        attendance = _safe_percentage(subjects)
        course_name = approved_admission.get("course", "Not Enrolled / Pending") if approved_admission else "Not Enrolled / Pending"
        fees_total = "Rs 1,20,000"
        fees_paid = "Rs 60,000"
        fees_pending = "Rs 60,000"
        subject_summary = ", ".join(
            f"{sub.get('name', '')} ({sub.get('progress', 0)}%)"
            for sub in subjects
        ) if subjects else "No subjects registered"
        subject_answer = ", ".join(
            f"{sub.get('name', '')} ({sub.get('progress', 0)}% attendance)"
            for sub in subjects
        ) if subjects else "no subjects registered."
        admission_status = admission.get("status", "No admission found") if admission else "No admission found"

        facts.extend([
            f"Admission status: {admission_status}",
            f"Course: {course_name}",
            f"Attendance: {attendance}",
            f"Subjects: {subject_summary}",
            f"Notifications: {_format_notification_list(notifications)}",
            f"Fees total: {fees_total}",
            f"Fees paid: {fees_paid}",
            f"Fees pending: {fees_pending}",
        ])

        direct_answer = {
            "attendance": f"Your current attendance is {attendance}.",
            "fee": f"Your fees are: total {fees_total}, paid {fees_paid}, pending {fees_pending}.",
            "fees": f"Your fees are: total {fees_total}, paid {fees_paid}, pending {fees_pending}.",
            "due": f"Your fees are: total {fees_total}, paid {fees_paid}, pending {fees_pending}.",
            "notification": "Your latest notifications are: " + _format_notification_list(notifications) + ".",
            "notifications": "Your latest notifications are: " + _format_notification_list(notifications) + ".",
            "subject": "Your current subjects are: " + subject_answer,
            "subjects": "Your current subjects are: " + subject_answer,
            "admission": f"Your admission status is {admission_status}.",
            "status": f"Your admission status is {admission_status}.",
        }

    elif role == "Admin":
        admissions = []
        async for doc in db.admissions.find().limit(50):
            admissions.append(doc)
        pending = [item for item in admissions if item.get("status") == "Pending"]
        approved = [item for item in admissions if item.get("status") == "Approved"]
        rejected = [item for item in admissions if item.get("status") == "Rejected"]
        staff_count = await db.users.count_documents({"role": "Staff"})

        facts.extend([
            f"Total admissions: {len(admissions)}",
            f"Pending admissions: {len(pending)}",
            f"Approved admissions: {len(approved)}",
            f"Rejected admissions: {len(rejected)}",
            f"Staff count: {staff_count}",
            "Recent admissions: " + (
                "; ".join(
                    f"{item.get('firstName', '')} {item.get('lastName', '')} - {item.get('course', 'N/A')} - {item.get('status', 'Pending')}"
                    for item in admissions[:5]
                ) if admissions else "No admissions yet"
            ),
        ])

        direct_answer = {
            "admission": f"There are {len(pending)} pending admissions, {len(approved)} approved admissions, and {len(rejected)} rejected admissions.",
            "admissions": f"There are {len(pending)} pending admissions, {len(approved)} approved admissions, and {len(rejected)} rejected admissions.",
            "pending": f"There are currently {len(pending)} pending admissions.",
            "staff": f"There are currently {staff_count} staff accounts.",
        }

    elif role == "Staff":
        schedule = []
        async for doc in db.schedules.find({"staff_email": email}):
            schedule.append(doc)
        approved_students = await db.admissions.count_documents({"status": "Approved"})

        facts.extend([
            f"Approved students count: {approved_students}",
            "Schedule: " + (
                "; ".join(
                    f"{item.get('time', 'N/A')} - {item.get('course', 'N/A')} in room {item.get('room', 'N/A')}"
                    for item in schedule
                ) if schedule else "No classes scheduled"
            ),
        ])

        direct_answer = {
            "schedule": "Your class schedule is: " + (
                "; ".join(f"{item.get('time', 'N/A')} - {item.get('course', 'N/A')} in room {item.get('room', 'N/A')}" for item in schedule)
                if schedule else "no classes scheduled."
            ),
            "student": f"There are {approved_students} approved students available in the system.",
            "students": f"There are {approved_students} approved students available in the system.",
            "class": "Your class schedule is: " + (
                "; ".join(f"{item.get('time', 'N/A')} - {item.get('course', 'N/A')} in room {item.get('room', 'N/A')}" for item in schedule)
                if schedule else "no classes scheduled."
            ),
        }

    return {
        "role": role,
        "email": email,
        "facts": facts,
        "direct_answer": direct_answer,
    }

def _try_direct_answer(question: str, direct_answer: dict):
    lowered = question.lower()
    for key, answer in direct_answer.items():
        if key in lowered:
            return answer
    return None

@app.get("/")
async def root():
    return {"message": "Welcome to EduManage API"}

# --- AUTHENTICATION ---
@app.post("/api/auth/register")
async def register_user(user: UserCreate):
    db = get_db()
    
    # Check if user already exists
    existing_user = await db.users.find_one({"email": user.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    # Hash password
    hashed_password = get_password_hash(user.password)
    
    # Create user document
    user_dict = {
        "name": user.name,
        "email": user.email,
        "password": hashed_password,
        "role": user.role
    }
    
    result = await db.users.insert_one(user_dict)
    if result.inserted_id:
        return {"message": "User registered successfully", "id": str(result.inserted_id)}
    raise HTTPException(status_code=500, detail="Failed to register user")

@app.post("/api/auth/login")
async def login_user(user: UserLogin):
    db = get_db()
    
    # Find user
    db_user = await db.users.find_one({"email": user.email})
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    # Verify password
    if not verify_password(user.password, db_user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    # Check role
    if db_user.get("role") != user.role:
        raise HTTPException(status_code=401, detail=f"User is not a {user.role}")
        
    # Generate token
    token = create_access_token(data={"sub": db_user["email"], "role": db_user["role"]})
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "name": db_user.get("name"),
            "email": db_user["email"],
            "role": db_user["role"]
        }
    }

# --- ADMISSIONS ---
@app.post("/api/admissions")
async def create_admission(admission: AdmissionCreate):
    db = get_db()
    admission_dict = admission.dict()
    result = await db.admissions.insert_one(admission_dict)
    if result.inserted_id:
        return {"message": "Admission created successfully", "id": str(result.inserted_id)}
    raise HTTPException(status_code=500, detail="Failed to create admission")

@app.get("/api/admissions")
async def get_admissions():
    db = get_db()
    cursor = db.admissions.find().sort("createdAt", -1).limit(50)
    admissions = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        admissions.append(doc)
    return admissions

# --- PAYMENTS ---
@app.post("/api/payments/create-order")
async def create_order(payment: PaymentCreate):
    try:
        data = {
            "amount": payment.amount * 100,  # Razorpay expects amount in paise
            "currency": payment.currency,
            "receipt": payment.receipt,
            "notes": payment.notes or {}
        }
        order = razorpay_client.order.create(data=data)
        return order
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- AI CHATBOT ---
@app.post("/api/ai/chat")
async def ai_chat(chat: ChatMessage, request: Request):
    db = get_db()
    token_payload = await _get_token_payload_from_request(request)
    user_context = await _build_user_context(db, token_payload)

    direct_answer = _try_direct_answer(chat.message, user_context.get("direct_answer", {}))
    if direct_answer:
        return {"reply": direct_answer}

    if not groq_client:
        return {
            "reply": "I can answer portal questions from current data, but the AI service is not configured right now. Try asking about admissions, attendance, fees, notifications, or schedules."
        }

    try:
        system_prompt = (
            "You are EduAI, the assistant for the EduManage college portal. "
            "Answer using the provided portal context first. "
            "Do not invent student names, fee numbers, attendance figures, notifications, or statuses that are not in context. "
            "If context is missing, say that clearly and offer the nearest helpful guidance. "
            "Keep answers concise, accurate, and specific to this portal."
        )

        context_prompt = "Portal context:\n" + "\n".join(f"- {fact}" for fact in user_context["facts"])
        history_messages = [
            {"role": turn.role, "content": turn.content}
            for turn in chat.history[-6:]
            if turn.content.strip()
        ]

        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": context_prompt},
                *history_messages,
                {"role": "user", "content": chat.message},
            ],
            model="llama-3.1-8b-instant",
            temperature=0.2,
            max_tokens=220,
        )

        reply = chat_completion.choices[0].message.content or "I couldn't generate a helpful answer just now."
        return {"reply": reply}
    except Exception:
        fallback = _try_direct_answer(chat.message, user_context.get("direct_answer", {}))
        if fallback:
            return {"reply": fallback}
        return {
            "reply": "I can help with admissions, attendance, fees, notifications, and schedules, but the AI response service is having trouble right now."
        }

# --- ADMIN DASHBOARD ---
@app.get("/api/admin/dashboard")
async def get_admin_dashboard(token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized to access Admin dashboard")
        
    db = get_db()
    
    total_students = 0
    total_staff = 0
    new_admissions = 0
    total_revenue = 0
    recent_admissions = []
    
    try:
        # Get actual counts
        total_students = await db.admissions.count_documents({"status": "Approved"})
        total_staff = await db.users.count_documents({"role": "Staff"}) if "users" in await db.list_collection_names() else 0
        new_admissions = await db.admissions.count_documents({})
        
        # Calculate revenue from successful payments (assuming a payments collection exists)
        if "payments" in await db.list_collection_names():
            pipeline = [{"$match": {"status": "captured"}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
            revenue_result = await db.payments.aggregate(pipeline).to_list(1)
            if revenue_result:
                total_revenue = revenue_result[0]["total"]
        
        # Get recent admissions
        cursor = db.admissions.find().sort("createdAt", -1).limit(5)
        async for doc in cursor:
            # Format the date nicely if it's a datetime object
            date_str = "N/A"
            if doc.get('createdAt'):
                try:
                    date_str = doc.get('createdAt').strftime("%b %d, %Y")
                except AttributeError:
                    # Fallback if stored as string instead of datetime
                    date_str = str(doc.get('createdAt'))[:10]
                    
            recent_admissions.append({
                "name": f"{doc.get('firstName', '')} {doc.get('lastName', '')}".strip(),
                "course": doc.get('course', 'N/A'),
                "date": date_str,
                "status": doc.get('status', 'Pending')
            })
    except Exception as e:
        print(f"Database error in admin dashboard: {e}")

    # Format revenue nicely
    formatted_revenue = f"₹{total_revenue:,.2f}"

    return {
        "stats": {
            "totalStudents": total_students,
            "totalStaff": total_staff,
            "revenue": formatted_revenue,
            "newAdmissions": new_admissions
        },
        "recentAdmissions": recent_admissions
    }

# --- ADMIN ADMISSIONS MANAGEMENT ---
@app.put("/api/admin/admissions/{admission_id}")
async def update_admission_status(
    admission_id: str, 
    status: str = Body(..., embed=True),
    token_payload: dict = Depends(verify_token)
):
    if token_payload.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized to access Admin dashboard")
        
    db = get_db()
    
    if not ObjectId.is_valid(admission_id):
        raise HTTPException(status_code=400, detail="Invalid admission ID")
        
    admission = await db.admissions.find_one({"_id": ObjectId(admission_id)})
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found")

    result = await db.admissions.update_one(
        {"_id": ObjectId(admission_id)},
        {"$set": {"status": status}}
    )
    
    if result.modified_count == 1:
        if status == "Approved":
            existing_user = await db.users.find_one({"email": admission["email"]})
            if not existing_user:
                await db.users.insert_one({
                    "name": f"{admission.get('firstName', '')} {admission.get('lastName', '')}".strip(),
                    "email": admission["email"],
                    "password": get_password_hash("password123"),
                    "role": "Student"
                })
        return {"message": "Status updated successfully"}
    raise HTTPException(status_code=404, detail="Admission not found")

@app.delete("/api/admin/admissions/{admission_id}")
async def delete_admission(
    admission_id: str,
    token_payload: dict = Depends(verify_token)
):
    if token_payload.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized to access Admin dashboard")
        
    db = get_db()
    
    if not ObjectId.is_valid(admission_id):
        raise HTTPException(status_code=400, detail="Invalid admission ID")
        
    result = await db.admissions.delete_one({"_id": ObjectId(admission_id)})
    if result.deleted_count == 1:
        return {"message": "Admission deleted successfully"}
    raise HTTPException(status_code=404, detail="Admission not found")

# --- ADMIN STAFF MANAGEMENT ---
@app.get("/api/admin/staff")
async def get_staff(token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized to access Admin dashboard")
        
    db = get_db()
    cursor = db.users.find({"role": "Staff"}, {"password": 0})
    staff = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        staff.append(doc)
    return staff

@app.post("/api/admin/staff")
async def create_staff(staff_user: UserCreate, token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized to access Admin dashboard")
        
    db = get_db()
    
    # Check if user already exists
    existing_user = await db.users.find_one({"email": staff_user.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    # Ensure role is Staff
    staff_user.role = "Staff"
        
    # Hash password
    hashed_password = get_password_hash(staff_user.password)
    
    user_dict = {
        "name": staff_user.name,
        "email": staff_user.email,
        "password": hashed_password,
        "role": staff_user.role
    }
    
    result = await db.users.insert_one(user_dict)
    if result.inserted_id:
        return {"message": "Staff created successfully", "id": str(result.inserted_id)}
    raise HTTPException(status_code=500, detail="Failed to create staff")

# --- STAFF DASHBOARD & ROUTES ---
@app.get("/api/staff/dashboard")
async def get_staff_dashboard(token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Staff":
        raise HTTPException(status_code=403, detail="Not authorized to access Staff dashboard")
        
    db = get_db()
    staff_email = token_payload.get("sub")
    
    # Get total students
    total_students_taught = await db.admissions.count_documents({"status": "Approved"})
    
    # Get staff schedule
    cursor = db.schedules.find({"staff_email": staff_email})
    schedule = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        schedule.append(doc)
    
    total_classes = len(schedule)
    
    return {
        "stats": {
            "totalStudents": total_students_taught,
            "totalClasses": total_classes,
            "upcomingClasses": total_classes  # Simplification for demo
        },
        "schedule": schedule
    }

@app.post("/api/staff/schedule")
async def add_schedule(schedule_data: ScheduleCreate, token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Staff" and token_payload.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Not authorized to add schedule")
    db = get_db()
    result = await db.schedules.insert_one(schedule_data.dict())
    if result.inserted_id:
        return {"message": "Schedule added", "id": str(result.inserted_id)}
    raise HTTPException(status_code=500, detail="Failed to add schedule")

@app.get("/api/staff/students")
async def get_staff_students(token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Staff":
        raise HTTPException(status_code=403, detail="Not authorized to access Staff data")
        
    db = get_db()
    cursor = db.admissions.find({"status": "Approved"}).sort("createdAt", -1)
    students = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        students.append(doc)
    return students


# --- STUDENT DASHBOARD & ROUTES ---
@app.get("/api/student/dashboard")
async def get_student_dashboard(token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Student":
        raise HTTPException(status_code=403, detail="Not authorized to access Student dashboard")
        
    db = get_db()
    user_email = token_payload.get("sub")
    
    admission = await db.admissions.find_one({"email": user_email, "status": "Approved"})
    course_name = admission.get("course", "N/A") if admission else "Not Enrolled / Pending"
    
    # Calculate attendance from subjects
    cursor = db.subjects.find({"student_email": user_email})
    subjects = []
    total_attendance = 0
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        subjects.append(doc)
        total_attendance += doc.get("progress", 0)
        
    avg_attendance = f"{int(total_attendance / len(subjects))}%" if subjects else "N/A"
    
    return {
        "profile": {
            "name": token_payload.get("name", "Student"),
            "email": user_email,
            "course": course_name
        },
        "stats": {
            "attendance": avg_attendance,
            "totalFees": "₹1,20,000",
            "feesPaid": "₹60,000",
            "feesPending": "₹60,000"
        },
        "subjects": subjects
    }

@app.get("/api/student/notifications")
async def get_student_notifications(token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") != "Student":
        raise HTTPException(status_code=403, detail="Not authorized")
    db = get_db()
    cursor = db.notifications.find({"student_email": token_payload.get("sub")}).sort("createdAt", -1).limit(10)
    notifications = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        notifications.append(doc)
    return notifications

@app.post("/api/student/subjects")
async def add_subject(subject_data: SubjectCreate, token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") not in ["Admin", "Staff"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    db = get_db()
    result = await db.subjects.insert_one(subject_data.dict())
    if result.inserted_id:
        return {"message": "Subject added", "id": str(result.inserted_id)}
    raise HTTPException(status_code=500, detail="Failed to add subject")

@app.post("/api/student/notifications")
async def add_notification(notification_data: NotificationCreate, token_payload: dict = Depends(verify_token)):
    if token_payload.get("role") not in ["Admin", "Staff"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    db = get_db()
    result = await db.notifications.insert_one(notification_data.dict())
    if result.inserted_id:
        return {"message": "Notification added", "id": str(result.inserted_id)}
    raise HTTPException(status_code=500, detail="Failed to add notification")
