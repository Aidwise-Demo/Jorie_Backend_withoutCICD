
import os
import requests
import logging
from typing import Dict
import msal

# Third Party Imports
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from firebase_admin.auth import EmailAlreadyExistsError
from firebase_admin import auth, credentials, initialize_app

# Firebase initialization
cred = credentials.Certificate("firebase_auth.json")
firebase_app = initialize_app(cred)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] - [%(name)s] - [%(levelname)s] - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Jorie Backend API",
    description="API for PowerBI Dashboard Links",
    version="1.0.1"
)

# OAuth2 scheme for token verification
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Request models
class UserRegister(BaseModel):
    email: str = Form(...)
    password: str = Form(...)
    display_name: str = Form(...)

class UserLogin(BaseModel):
    email: str = Form(...)
    password: str = Form(...)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from dotenv import load_dotenv

load_dotenv()  # Loads variables from .env

TENANT_ID = os.getenv('TENANT_ID')
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
AUTHORITY = os.getenv('AUTHORITY')
SCOPE = [os.getenv('SCOPE')]
WORKSPACE_ID = os.getenv('WORKSPACE_ID')

DASHBOARD_REPORTS = {
    "patientRiskProfiler": os.getenv("DASHBOARD_REPORT_PATIENT_RISK_PROFILER"),
    "patientTimeline": os.getenv("DASHBOARD_REPORT_PATIENT_TIMELINE"),
    "personaComparison": os.getenv("DASHBOARD_REPORT_PERSONA_COMPARISON"),
    "adherenceScorecard": os.getenv("DASHBOARD_REPORT_ADHERENCE_SCORECARD"),
    "risk_prediction": os.getenv("DASHBOARD_REPORT_RISK_PREDICTION")
}

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY_JORIE_JAYAM")

# === Get access token using MSAL ===
def get_access_token():
    app_msal = msal.ConfidentialClientApplication(
        CLIENT_ID, authority=AUTHORITY,
        client_credential=CLIENT_SECRET
    )
    token_response = app_msal.acquire_token_for_client(scopes=SCOPE)
    return token_response.get("access_token", None)

# === Get embed token for Power BI report ===
def get_embed_token(access_token, report_id):
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}/reports/{report_id}/GenerateToken"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    data = {
        "accessLevel": "view"
    }
    response = requests.post(url, json=data, headers=headers)
    return response.json()

# === Get report embed URL ===
def get_report_embed_url(access_token, report_id):
    report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}/reports/{report_id}"
    report_response = requests.get(report_url, headers={"Authorization": f"Bearer {access_token}"})
    report = report_response.json()
    return report.get('embedUrl', '')

# Dependency to verify Firebase ID Token
def verify_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    try:
        token = authorization.split("Bearer ")[1]  # Extract the token
        decoded_token = auth.verify_id_token(token)  # Verify token with Firebase
        return decoded_token  # Returns user info if valid
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

@app.get("/")
async def root():
    return {"message": "Welcome to Jorie Backend API"}

@app.post("/api/register")
async def register(
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...)
):
    try:
        logger.info(f"Registering new user with email: {email}")
        
        user_record = auth.create_user(
            email=email,
            password=password,
            display_name=display_name
        )
                
        return {
            "message": "User registered successfully",
            "uid": user_record.uid,
        }
    
    except EmailAlreadyExistsError:
        logger.exception(f"Email already in use: [{email}]")
        raise HTTPException(
            status_code=400, 
            detail="Email already in use"
        )
       
    except Exception as e:
        logger.exception(f"Registration error: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=f"Registration failed: {str(e)}"
        )

@app.post("/api/login")
async def login(
    email: str = Form(...),
    password: str = Form(...)
):
    try:
        logger.info(f"Login attempt for user: [{email}]")

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True
        }
        response = requests.post(url, json=payload)
        data = response.json()

        if response.status_code == 200:
            logger.info(f"Login successful for user: [{email}]")
            return {
                "id_token": data["idToken"],
                "refresh_token": data["refreshToken"],
                "expires_in": data["expiresIn"]
            }
        else:
            logger.error(f"Login failed for user: [{email}]")
            raise HTTPException(status_code=400, detail=data.get("error", {}).get("message", "Login failed"))
        
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )

@app.get("/api/dashboards/{name}")
async def get_dashboard(name: str, token_data: dict = Depends(verify_token)):
    """
    Get PowerBI dashboard embed info by name. 
    Requires valid Firebase authentication token.
    Returns embed token, embed URL, and report ID for Power BI embedding.
    """
    try:
        logger.info(f"Requesting dashboard embed info for: {name} by user: {token_data['uid']}")
        
        if name not in DASHBOARD_REPORTS:
            logger.error(f"Dashboard {name} not found")
            raise HTTPException(
                status_code=404,
                detail=f"Dashboard '{name}' not found. Available dashboards: {list(DASHBOARD_REPORTS.keys())}"
            )
        
        report_id = DASHBOARD_REPORTS[name]
        
        # Get Azure AD access token
        access_token = get_access_token()
        if not access_token:
            logger.error("Failed to get Azure AD token")
            raise HTTPException(status_code=500, detail="Failed to get Azure AD token")
        
        # Get embed token
        embed_token_response = get_embed_token(access_token, report_id)
        if 'token' not in embed_token_response:
            logger.error("Failed to get embed token")
            raise HTTPException(status_code=500, detail="Failed to get embed token")
        
        # Get report embed URL
        embed_url = get_report_embed_url(access_token, report_id)
        if not embed_url:
            logger.error("Failed to get embed URL")
            raise HTTPException(status_code=500, detail="Failed to get embed URL")
        
        return {
            "embedToken": embed_token_response['token'],
            "embedUrl": embed_url,
            "reportId": report_id,
            "workspaceId": WORKSPACE_ID
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))